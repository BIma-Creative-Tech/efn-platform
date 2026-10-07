// Load test Fase 1 EFN (gelombang 13, efn-devsecops-engineer). Dijalankan oleh tests/load/run.sh di STACK DEV saja.
//
// Skenario (paralel, executor constant-vus):
//   login  : GET /v1/auth/challenge -> pecahkan ALTCHA (SHA-256, k6/crypto) -> POST /v1/auth/otp/request (nomor acak
//            unik) -> POST /v1/auth/otp/verify dengan kode SALAH (kode asli hanya ada di log BFF dev; jalur verify
//            tetap diukur: lookup challenge + hitung percobaan).
//   pos    : server-to-server Odoo -> BFF (header X-EFN-Service-Key = secret dev odoo_bff_api_key): otp/request,
//            loyalty/balance, loyalty/earn (idempoten, member uji 628999*), discount-token/consume token palsu (409).
//   scout  : POST /v1/market-scout/reports (JPEG sintetis 17 KB, multipart) dengan JWT member uji -> 202 queued;
//            OCR berjalan async di ocr-worker (antrean ocr.requested).
//   edge   : GET https://api.efn.local/health lewat Traefik websecure (overhead edge + TLS), dibatasi di bawah
//            middleware ratelimit-api (30 rps/IP) karena semua VU berasal dari satu IP.
// Klien publik (login/scout) langsung ke https://api-gateway:3000 dari network `internal` (subnet TRUSTED_PROXIES,
// sama seperti Traefik) dengan X-Forwarded-For = IP publik acak PER ITERASI -> rate limit per IP BFF bekerja seperti
// produksi (banyak klien), bukan satu IP yang langsung 429. Rate limit Traefik per IP diukur terpisah (edge).
// TLS: semua HTTPS terverifikasi (SSL_CERT_FILE = CA edge dev + CA internal; tanpa insecureSkipTLSVerify).
import http from 'k6/http';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import exec from 'k6/execution';

const API = __ENV.EFN_API || 'https://api.efn.local';
const BFF = __ENV.EFN_BFF || 'https://api-gateway:3000';
const SERVICE_KEY = __ENV.EFN_SERVICE_KEY || '';
const JWT = __ENV.EFN_JWT || '';
const DURATION = __ENV.EFN_DURATION || '5m';
const VU_LOGIN = parseInt(__ENV.EFN_VU_LOGIN || '20', 10);
const VU_POS = parseInt(__ENV.EFN_VU_POS || '30', 10);
const VU_SCOUT = parseInt(__ENV.EFN_VU_SCOUT || '5', 10);
const EDGE_RPS = parseInt(__ENV.EFN_EDGE_RPS || '20', 10);
const DEBUG = __ENV.EFN_DEBUG === '1';
const PHOTO = open('../fixtures/tag.jpg', 'b');

const rateLimited = new Counter('efn_rate_limited');
const unexpected = new Counter('efn_unexpected_status');

function scenario(fn, vus) {
  return { executor: 'constant-vus', exec: fn, vus, duration: DURATION, gracefulStop: '30s' };
}
const scenarios = {};
if (VU_LOGIN > 0) scenarios.login = scenario('login', VU_LOGIN);
if (VU_POS > 0 && SERVICE_KEY) scenarios.pos = scenario('pos', VU_POS);
if (VU_SCOUT > 0 && JWT) scenarios.scout = scenario('scout', VU_SCOUT);
if (EDGE_RPS > 0) {
  scenarios.edge = {
    executor: 'constant-arrival-rate', exec: 'edge', rate: EDGE_RPS, timeUnit: '1s', duration: DURATION,
    preAllocatedVUs: 5, maxVUs: 20,
  };
}

export const options = {
  scenarios,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  // Ambang = target Fase 1 usulan (bukan SLA kontraktual): gagal -> exit code != 0, dicatat di laporan.
  thresholds: {
    'http_req_failed{scenario:login}': ['rate<0.01'],
    'http_req_failed{scenario:pos}': ['rate<0.01'],
    'http_req_failed{scenario:scout}': ['rate<0.01'],
    'http_req_failed{scenario:edge}': ['rate<0.01'],
    'http_req_duration{scenario:login,ep:otp_request}': ['p(95)<800'],
    'http_req_duration{scenario:pos,ep:otp_request}': ['p(95)<500'],
    'http_req_duration{scenario:pos,ep:balance}': ['p(95)<300'],
    'http_req_duration{scenario:pos,ep:earn}': ['p(95)<500'],
    'http_req_duration{scenario:scout,ep:report}': ['p(95)<1500'],
  },
};

// Jalur negatif yang memang diharapkan (verify kode salah 400, consume token palsu 409) bukan "failed".
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 400, 409));

function rnd(n) {
  return Math.floor(Math.random() * n);
}
function msisdn(prefix) {
  // 62 + 10-11 digit. 628999* = member uji POS load test, 628120* = login (tidak bertabrakan dengan smoke 62812xxxx)
  return `${prefix}${String(exec.vu.idInTest % 1000).padStart(3, '0')}${String(rnd(1e5)).padStart(5, '0')}`;
}
function clientIp() {
  // IP publik dokumentasi/acak di luar rentang privat (TEST-NET 198.18.0.0/15 = benchmark, RFC 2544)
  return `198.${18 + rnd(2)}.${rnd(256)}.${1 + rnd(254)}`;
}
function uuid4() {
  const b = new Uint8Array(crypto.randomBytes(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const x = Array.from(b, (v) => v.toString(16).padStart(2, '0')).join('');
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}
function json(r) {
  try {
    return r.json();
  } catch (_) {
    return {};
  }
}
let dbgLeft = 3;
function ok(r, name, cond) {
  if (r.status === 429) rateLimited.add(1);
  const passed = check(r, { [name]: cond });
  if (!passed) {
    unexpected.add(1, { status: String(r.status) });
    if (DEBUG && dbgLeft-- > 0) console.warn(`${name}: HTTP ${r.status} ${String(r.body).slice(0, 200)}`);
  }
  return passed;
}

function solveAltcha(ch) {
  for (let n = 0; n <= ch.maxnumber; n++) {
    if (crypto.sha256(ch.salt + n, 'hex') === ch.challenge) {
      const p = { algorithm: ch.algorithm, challenge: ch.challenge, salt: ch.salt, signature: ch.signature, number: n };
      return encoding.b64encode(JSON.stringify(p));
    }
  }
  return null;
}

export function login() {
  const hdr = { 'Content-Type': 'application/json', 'X-Forwarded-For': clientIp() };
  const c = http.get(`${BFF}/v1/auth/challenge`, { headers: hdr, tags: { ep: 'challenge' } });
  if (!ok(c, 'challenge 200', (r) => r.status === 200)) return;
  const altcha = solveAltcha(json(c));
  if (!check(altcha, { 'altcha terpecahkan': (a) => a !== null })) return;
  const req = http.post(`${BFF}/v1/auth/otp/request`, JSON.stringify({ phone: msisdn('628120'), altcha }),
    { headers: hdr, tags: { ep: 'otp_request' } });
  if (!ok(req, 'otp/request 202', (r) => r.status === 202)) return;
  const v = http.post(`${BFF}/v1/auth/otp/verify`, JSON.stringify({ challenge_id: json(req).challenge_id, code: '000000' }),
    { headers: hdr, tags: { ep: 'otp_verify' } });
  ok(v, 'otp/verify kode salah ditolak 400', (r) => r.status === 400);
}

export function pos() {
  const hdr = { 'Content-Type': 'application/json', 'X-EFN-Service-Key': SERVICE_KEY };
  const member = msisdn('628999');
  const order = uuid4();
  const company = ['EFN', 'RTL', 'FNB'][rnd(3)];
  const posConfig = 100000 + exec.vu.idInTest * 1000 + rnd(1000);
  const o = http.post(`${BFF}/v1/pos/otp/request`, JSON.stringify({
    member_ref: member, order_ref: order, amount: 150000, company_code: company, pos_config_id: posConfig,
  }), { headers: hdr, tags: { ep: 'otp_request' } });
  ok(o, 'pos otp/request 202', (r) => r.status === 202);
  const b = http.post(`${BFF}/v1/pos/loyalty/balance`,
    JSON.stringify({ member_ref: member, pos_config_id: posConfig, company_code: company }),
    { headers: hdr, tags: { ep: 'balance' } });
  ok(b, 'balance 200', (r) => r.status === 200);
  const e = http.post(`${BFF}/v1/pos/loyalty/earn`, JSON.stringify({
    idempotency_key: `pos_earn:${order}`, order_ref: order, member_ref: member, company_code: company,
    eligible_amount: 150000,
  }), { headers: hdr, tags: { ep: 'earn' } });
  ok(e, 'earn 200', (r) => r.status === 200);
  const c = http.post(`${BFF}/v1/pos/discount-token/consume`, JSON.stringify({
    discount_token: 'eyJhbGciOiJIUzI1NiJ9.eyJ4IjoxfQ.invalid', order_ref: order, member_ref: member, amount_total: 150000,
  }), { headers: hdr, tags: { ep: 'consume' } });
  ok(c, 'consume token palsu ditolak 409', (r) => r.status === 409);
}

export function scout() {
  const body = {
    product_name: `Beras Uji ${rnd(1e6)}`,
    price: String(10000 + rnd(90000)),
    competitor_name: 'Toko Load Test',
    lat: '-6.2',
    lng: '106.8',
    photo: http.file(PHOTO, `tag-${uuid4()}.jpg`, 'image/jpeg'),
  };
  const r = http.post(`${BFF}/v1/market-scout/reports`, body, {
    headers: { Authorization: `Bearer ${JWT}`, 'X-Forwarded-For': clientIp() }, tags: { ep: 'report' },
  });
  ok(r, 'report 202', (x) => x.status === 202);
}

export function edge() {
  const r = http.get(`${API}/health`, { tags: { ep: 'health' } });
  ok(r, 'edge /health 200', (x) => x.status === 200);
}
