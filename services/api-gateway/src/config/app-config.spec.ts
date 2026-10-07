import { createHash, hkdfSync } from 'node:crypto';
import { AppConfig, ConfigError, tlsKeyPairCheck } from './app-config';
import { randomSecret, testConfig } from '../common/testing/fakes';
import { memberHash } from '../common/pii/pii';

/**
 * Material TLS palsu: hanya bentuk PEM (bukan sertifikat/kunci sungguhan). Tidak ada kunci privat di repo
 * (body sengaja pendek sehingga juga tidak cocok pola gitleaks).
 */
const CA_PEM = Buffer.from('-----BEGIN CERTIFICATE-----\ndummy-ca\n-----END CERTIFICATE-----\n');
const SERVER_CERT = Buffer.from('-----BEGIN CERTIFICATE-----\ndummy-server\n-----END CERTIFICATE-----\n');
const SERVER_KEY = Buffer.from('-----BEGIN PRIVATE KEY-----\ndummy\n');
// Salinan baru per baca: AppConfig me-nol-kan buffer file secret setelah dipakai (SEC-29), jadi konstanta tidak boleh dibagi.
const caReader = (): Buffer => Buffer.from(CA_PEM);
/** Path fallback secret kontrak (`/run/secrets/<nama>`) yang kini dibaca lewat reader AppConfig (SEC-29). */
const SECRET_FALLBACK_RE = /^\/run\/secrets\/(pg|redis|rabbitmq|minio)_/;

/** Rentang Cloudflare publik (https://www.cloudflare.com/ips/) — contoh daftar panjang untuk TRUSTED_PROXIES. */
const CLOUDFLARE_V4 = [
  '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22', '141.101.64.0/18', '108.162.192.0/18',
  '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
  '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
];
const CLOUDFLARE_V6 = [
  '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32', '2a06:98c0::/29', '2c0f:f248::/32',
];

describe('AppConfig — secret dari file ke memori, tidak ke env (SEC-29)', () => {
  const JWT = 'jwt-value-0123456789abcdef';
  const PEPPER = 'pepper-value-0123456789abcdef';
  const files: Record<string, string> = {
    '/custom/jwt': `${JWT}\n`,
    '/run/secrets/otp_pepper': PEPPER,
    '/run/secrets/pg_password_bff': 'pg-pw-0123456789abcdef',
  };
  const reader = (p: string): Buffer => {
    if (!(p in files)) throw new Error('ENOENT');
    return Buffer.from(files[p]);
  };

  it('X_FILE dan fallback /run/secrets/<nama kontrak> dibaca; env TIDAK pernah ditulis', () => {
    const env: Record<string, string | undefined> = { NODE_ENV: 'test', JWT_SECRET_FILE: '/custom/jwt', PG_HOST: 'postgres', PG_SSLMODE: 'disable', EFN_ALLOW_PLAINTEXT: 'true' };
    const before = JSON.stringify(env);
    const cfg = new AppConfig(env, reader);
    expect(cfg.jwtSecret).toBe(JWT);
    expect(cfg.otpPepper).toBe(PEPPER);
    expect(cfg.pgConnection.password).toBe('pg-pw-0123456789abcdef');
    expect(() => cfg.whatsappToken).toThrow('belum dikonfigurasi');
    expect(JSON.stringify(env)).toBe(before);
  });

  it('fromProcessEnv tidak mengubah process.env', () => {
    const before = { ...process.env };
    void AppConfig.fromProcessEnv();
    expect(process.env).toEqual(before);
  });

  it('buffer mentah file di-nol-kan dan file dibaca sekali (cache)', () => {
    const raws: Buffer[] = [];
    const cfg = new AppConfig({ JWT_SECRET_FILE: '/custom/jwt' }, (p) => {
      const b = reader(p);
      raws.push(b);
      return b;
    });
    expect(cfg.jwtSecret).toBe(JWT);
    expect(cfg.jwtSecret).toBe(JWT);
    expect(raws).toHaveLength(1);
    expect(raws[0].every((x) => x === 0)).toBe(true);
  });

  it('X_FILE tak terbaca → ConfigError dengan nama/path, bukan nilai', () => {
    expect(() => new AppConfig({ JWT_SECRET_FILE: '/hilang' }, reader).jwtSecret).toThrow('JWT_SECRET_FILE (/hilang) tidak dapat dibaca');
  });

  it('env literal secret hanya APP_ENV=dev; di luar dev ditolak (getter & assertStartupSecrets), file pun tidak menyelamatkan', () => {
    expect(new AppConfig({ APP_ENV: 'dev', JWT_SECRET: JWT }, reader).jwtSecret).toBe(JWT);
    for (const appEnv of [undefined, 'staging', 'production']) {
      const env: Record<string, string | undefined> = { JWT_SECRET: JWT, JWT_SECRET_FILE: '/custom/jwt', PG_HOST: 'postgres' };
      if (appEnv) env.APP_ENV = appEnv;
      const cfg = new AppConfig(env, reader);
      expect(() => cfg.jwtSecret).toThrow('ditolak di luar APP_ENV=dev');
      let msg = '';
      try {
        cfg.assertStartupSecrets();
      } catch (err) {
        msg = (err as Error).message;
      }
      expect(msg).toContain('JWT_SECRET sebagai env literal ditolak');
      expect(msg).not.toContain(JWT);
    }
    expect(() => new AppConfig({ EFN_BFF_API_KEY: JWT }, reader).odooBffApiKey).toThrow('ditolak di luar APP_ENV=dev');
  });

  it('password di URL koneksi (DATABASE_URL/REDIS_URL/AMQP_URL) hanya APP_ENV=dev', () => {
    const plain = { NODE_ENV: 'test', EFN_ALLOW_PLAINTEXT: 'true', PG_SSLMODE: 'disable' };
    expect(() => new AppConfig({ ...plain, DATABASE_URL: 'postgresql://u:pw@db/x' }, reader).pgConnection).toThrow('tidak boleh memuat password');
    expect(() => new AppConfig({ ...plain, REDIS_URL: 'redis://u:pw@redis:6379' }, reader).redisConnection).toThrow('tidak boleh memuat password');
    expect(() => new AppConfig({ ...plain, AMQP_URL: 'amqp://u:pw@rabbitmq' }, reader).amqpConnection).toThrow('tidak boleh memuat password');
    const noFiles = (): Buffer => {
      throw new Error('ENOENT');
    };
    expect(new AppConfig({ ...plain, APP_ENV: 'dev', DATABASE_URL: 'postgresql://u:pw@db/x' }, noFiles).pgConnection.password).toBe('pw');
    // file secret tetap menang atas password URL (urutan lama)
    expect(new AppConfig({ ...plain, APP_ENV: 'dev', DATABASE_URL: 'postgresql://u:pw@db/x' }, reader).pgConnection.password).toBe(
      'pg-pw-0123456789abcdef',
    );
  });
});

describe('AppConfig', () => {
  it('assertStartupSecrets menyebut nama secret yang hilang, bukan nilainya', () => {
    const cfg = new AppConfig({ APP_ENV: 'dev', JWT_SECRET: 'short', PG_HOST: 'postgres' }, () => {
      throw new Error('ENOENT');
    });
    let msg = '';
    try {
      cfg.assertStartupSecrets();
    } catch (err) {
      expect(err).toBeInstanceOf(ConfigError);
      msg = (err as Error).message;
    }
    expect(msg).toContain('OTP_PEPPER');
    expect(msg).toContain('JWT_SECRET kurang dari');
    expect(msg).not.toContain('short');
  });

  it('konfigurasi PG dari PG_HOST/PG_PORT/PG_DB/PG_USER + PG_PASSWORD, TLS verify-full dengan CA', () => {
    const cfg = testConfig(
      { PG_HOST: 'postgres', PG_PORT: '5433', PG_DB: 'efn_platform', PG_USER: 'efn_bff', PG_PASSWORD: 'pw' },
      caReader,
    );
    expect(cfg.pgConnection).toMatchObject({
      host: 'postgres',
      port: 5433,
      database: 'efn_platform',
      user: 'efn_bff',
      password: 'pw',
      ssl: { ca: CA_PEM, rejectUnauthorized: true, minVersion: 'TLSv1.2', servername: 'postgres' },
    });
  });

  it('AMQP_URL amqps tanpa password digabung dengan RABBITMQ_PASSWORD; username = user_id publish', () => {
    const cfg = testConfig({ AMQP_URL: 'amqps://efn-bff@rabbitmq:5671', RABBITMQ_PASSWORD: 'p@ss/w0rd' }, caReader);
    expect(cfg.amqpConnection).toEqual({
      url: 'amqps://rabbitmq:5671',
      username: 'efn-bff',
      password: 'p@ss/w0rd',
      tls: { ca: CA_PEM, rejectUnauthorized: true, minVersion: 'TLSv1.2', servername: 'rabbitmq' },
    });
    expect(() => testConfig({ AMQP_URL: 'amqps://rabbitmq:5671' }, caReader).amqpConnection).toThrow('username');
  });

  it('TRUSTED_PROXIES default hanya loopback + RFC1918 + ULA; bisa di-override', () => {
    expect(testConfig().trustProxy).toEqual(['127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7']);
    expect(testConfig({ TRUSTED_PROXIES: '172.20.0.0/16, 10.1.0.0/16' }).trustProxy).toEqual(['172.20.0.0/16', '10.1.0.0/16']);
  });

  it('SEC-03: TRUSTED_PROXIES mendukung daftar CIDR panjang (Cloudflare v4+v6), pemisah koma/spasi/baris baru', () => {
    const list = [...CLOUDFLARE_V4, ...CLOUDFLARE_V6, '172.16.0.0/12'].join(',\n ');
    const cfg = testConfig({ TRUSTED_PROXIES: list });
    expect(cfg.trustProxy).toHaveLength(CLOUDFLARE_V4.length + CLOUDFLARE_V6.length + 1);
    expect(cfg.trustProxy).toContain('2a06:98c0::/29');
    expect(testConfig({ TRUSTED_PROXIES: '10.0.0.0/8; 10.0.0.0/8 loopback' }).trustProxy).toEqual(['10.0.0.0/8', 'loopback']);
  });

  it.each([['10.0.0.0/33'], ['2400:cb00::/129'], ['bukan-ip'], ['10.0.0.0/8,*'], ['1.2.3.4/x']])(
    'TRUSTED_PROXIES entri tidak valid (%s) → ConfigError (gagal start)',
    (v) => {
      expect(() => testConfig({ TRUSTED_PROXIES: v }).trustProxy).toThrow(ConfigError);
    },
  );

  it('kunci OTP dan pepper member_hash diturunkan via HKDF dengan info berbeda', () => {
    const pepper = randomSecret();
    const cfg = testConfig({ OTP_PEPPER: pepper });
    const expectedMember = Buffer.from(hkdfSync('sha256', pepper, Buffer.alloc(0), 'efn:member_hash:v1', 32)).toString('hex');
    expect(cfg.memberHashPepper).toBe(expectedMember);
    expect(cfg.otpCodeKey.toString('hex')).not.toBe(expectedMember);
    expect(cfg.memberHashPepper).not.toBe(pepper);
    // sanity: member_hash = sha256(pepper_turunan + member_ref)
    expect(createHash('sha256').update(expectedMember + '6281234567890').digest('hex')).toMatch(/^[0-9a-f]{64}$/);
  });

  it('test vector lintas-bahasa member_hash (sama dengan services/etl/tests/test_jobs.py)', () => {
    // Dihitung independen (HKDF RFC 5869 manual dengan HMACSHA256 .NET). Mengubah nilai ini = memutus join ETL.
    // Pepper ≥16 karakter (batas MIN_SECRET_LENGTH).
    const cfg = testConfig({ OTP_PEPPER: 'test-otp-pepper-v1' });
    expect(cfg.memberHashPepper).toBe('46df3d2fee0e91a91083827a5f303586e9c6b5c54afe096896bac9feb724061f');
    expect(memberHash(cfg.memberHashPepper, '6281234567890')).toBe(
      '8930b421545b77ea6ff5093510c261afe67f1b1e3e18c925cb76d59516926653',
    );
    expect(cfg.otpCodeKey.toString('hex')).toBe('bd7f6eb6a047417ccd0c25a6504b126efceb6c91e21338259c39e9b9c55fadbb');
  });

  it('HKDF salt kosong sesuai RFC 5869 A.3', () => {
    const okm = Buffer.from(hkdfSync('sha256', Buffer.alloc(22, 0x0b), Buffer.alloc(0), Buffer.alloc(0), 42));
    expect(okm.toString('hex')).toBe(
      '8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8',
    );
  });

  it('bucket market scout dari MINIO_BUCKET_MARKET_SCOUT', () => {
    expect(testConfig({ MINIO_BUCKET_MARKET_SCOUT: 'ms-staging' }).marketScoutBucket).toBe('ms-staging');
    expect(testConfig().marketScoutBucket).toBe('market-scout');
  });
});

describe('AppConfig — TLS builder (docs/04 §7.1)', () => {
  const tlsEnv = {
    PG_HOST: 'postgres',
    REDIS_URL: 'rediss://redis:6379',
    REDIS_USERNAME: 'efn-bff',
    REDIS_PASSWORD: 'redis-pw',
    AMQP_URL: 'amqps://efn-bff@rabbitmq:5671',
    MINIO_ENDPOINT: 'minio',
    MINIO_PORT: '9000',
    TLS_CERT_FILE: '/run/secrets/tls_api_gateway_crt',
    TLS_KEY_FILE: '/run/secrets/tls_api_gateway_key',
  };
  const plaintextEnv = {
    PG_HOST: 'postgres',
    PG_SSLMODE: 'disable',
    REDIS_URL: 'redis://redis:6379',
    AMQP_URL: 'amqp://efn-bff@rabbitmq:5672',
    MINIO_USE_SSL: 'false',
  };

  it('default: semua klien TLS dengan CA /run/secrets/efn_ca_crt, servername = host, TLS ≥ 1.2', () => {
    const paths: string[] = [];
    const cfg = testConfig({ ...tlsEnv, TLS_CERT_FILE: '', TLS_KEY_FILE: '' }, (p) => {
      if (SECRET_FALLBACK_RE.test(p)) throw new Error('ENOENT'); // fallback secret kontrak (SEC-29), bukan CA
      paths.push(p);
      return Buffer.from(CA_PEM);
    });
    expect(cfg.redisConnection).toEqual({
      url: 'rediss://redis:6379',
      username: 'efn-bff',
      password: 'redis-pw',
      tls: { ca: CA_PEM, rejectUnauthorized: true, minVersion: 'TLSv1.2', servername: 'redis' },
    });
    expect(cfg.minioTransport).toEqual({
      endPoint: 'minio',
      port: 9000,
      useSSL: true,
      tls: { ca: CA_PEM, rejectUnauthorized: true, minVersion: 'TLSv1.2', servername: 'minio' },
    });
    expect(cfg.pgConnection.ssl).not.toBe(false);
    expect(new Set(paths)).toEqual(new Set(['/run/secrets/efn_ca_crt']));
  });

  it('*_CA_FILE per koneksi dihormati', () => {
    const paths: string[] = [];
    const cfg = testConfig({ ...tlsEnv, REDIS_TLS_CA_FILE: '/x/redis-ca.pem', PG_SSLROOTCERT: '/x/pg-ca.pem' }, (p) => {
      if (SECRET_FALLBACK_RE.test(p)) throw new Error('ENOENT');
      paths.push(p);
      return Buffer.from(CA_PEM);
    });
    void cfg.redisConnection;
    void cfg.pgConnection;
    expect(paths).toEqual(['/x/redis-ca.pem', '/x/pg-ca.pem']);
  });

  it('CA tidak terbaca / bukan PEM → ConfigError (gagal start, tanpa fallback plaintext)', () => {
    expect(() => testConfig(tlsEnv).redisConnection).toThrow(/Redis: CA .* tidak dapat dibaca/);
    expect(() => testConfig(tlsEnv, () => Buffer.from('bukan sertifikat')).pgConnection).toThrow('bukan sertifikat PEM');
    expect(() => testConfig(tlsEnv).assertTransportSecurity()).toThrow(ConfigError);
  });

  it('tanpa EFN_ALLOW_PLAINTEXT: redis:// / amqp:// / PG_SSLMODE=disable / MINIO_USE_SSL=false / tanpa sertifikat server ditolak', () => {
    const cfg = testConfig(plaintextEnv, caReader);
    expect(() => cfg.redisConnection).toThrow('koneksi tanpa TLS ditolak');
    expect(() => cfg.amqpConnection).toThrow('koneksi tanpa TLS ditolak');
    expect(() => cfg.pgConnection).toThrow('koneksi tanpa TLS ditolak');
    expect(() => cfg.minioTransport).toThrow('koneksi tanpa TLS ditolak');
    expect(() => cfg.httpsServerOptions).toThrow('koneksi tanpa TLS ditolak');
    expect(() => testConfig({ PG_HOST: 'postgres', PG_SSLMODE: 'require' }, caReader).pgConnection).toThrow('PG_SSLMODE harus verify-full');
  });

  it('EFN_ALLOW_PLAINTEXT=true di non-production → plaintext diizinkan dan dilaporkan', () => {
    const cfg = testConfig({ ...plaintextEnv, EFN_ALLOW_PLAINTEXT: 'true', NODE_ENV: 'development' }, caReader);
    expect(cfg.redisConnection.tls).toBeUndefined();
    expect(cfg.pgConnection.ssl).toBe(false);
    expect(cfg.assertTransportSecurity().plaintextChannels).toEqual([
      'PostgreSQL',
      'Redis',
      'RabbitMQ',
      'MinIO',
      'BFF HTTP server',
    ]);
  });

  it.each([
    [{ NODE_ENV: 'production' }],
    [{ NODE_ENV: 'staging', APP_ENV: 'production' }],
    [{ NODE_ENV: '' }], // NODE_ENV kosong = production (fail safe)
  ])('production menolak EFN_ALLOW_PLAINTEXT=true (%j)', (envOverride) => {
    const cfg = testConfig({ ...plaintextEnv, EFN_ALLOW_PLAINTEXT: 'true', ...envOverride }, caReader);
    expect(() => cfg.allowPlaintext).toThrow('EFN_ALLOW_PLAINTEXT=true ditolak');
    expect(() => cfg.redisConnection).toThrow('ditolak');
    expect(() => cfg.assertTransportSecurity()).toThrow(ConfigError);
  });

  it('production + TLS lengkap → lolos; sertifikat server dibaca dari TLS_CERT_FILE/TLS_KEY_FILE', () => {
    const files: Record<string, Buffer> = {
      '/run/secrets/efn_ca_crt': CA_PEM,
      '/run/secrets/tls_api_gateway_crt': SERVER_CERT,
      '/run/secrets/tls_api_gateway_key': SERVER_KEY,
    };
    const cfg = testConfig({ ...tlsEnv, NODE_ENV: 'production' }, (p) => {
      if (!(p in files)) throw new Error('ENOENT');
      return Buffer.from(files[p]);
    });
    // Validasi pasangan cert/key (OpenSSL) diganti stub: repo tidak boleh memuat kunci privat, bahkan dummy.
    const original = tlsKeyPairCheck.verify;
    const verify = jest.fn();
    tlsKeyPairCheck.verify = verify;
    try {
      expect(cfg.assertTransportSecurity()).toEqual({ plaintextChannels: [] });
      expect(cfg.httpsServerOptions).toEqual({ cert: SERVER_CERT, key: SERVER_KEY, minVersion: 'TLSv1.2' });
      expect(verify).toHaveBeenCalledWith(SERVER_CERT, SERVER_KEY);
    } finally {
      tlsKeyPairCheck.verify = original;
    }
  });

  it('sertifikat server: hanya satu dari cert/key, tidak terbaca, atau ditolak OpenSSL → ConfigError', () => {
    expect(() => testConfig({ TLS_CERT_FILE: '/c' }, caReader).httpsServerOptions).toThrow('berpasangan');
    expect(() => testConfig({ TLS_CERT_FILE: '/c', TLS_KEY_FILE: '/k' }).httpsServerOptions).toThrow('tidak dapat dibaca');
    // PEM berbentuk benar tetapi isinya bukan sertifikat/kunci → createSecureContext asli melempar
    const bogus = testConfig({ TLS_CERT_FILE: '/c', TLS_KEY_FILE: '/k' }, (p) => (p === '/c' ? SERVER_CERT : SERVER_KEY));
    expect(() => bogus.httpsServerOptions).toThrow('tidak valid atau tidak berpasangan');
  });

  it('WhatsApp Graph API wajib https (verifikasi sertifikat publik default)', () => {
    expect(testConfig().whatsappGraphBaseUrl).toBe('https://graph.facebook.com');
    expect(() => testConfig({ WHATSAPP_GRAPH_BASE_URL: 'http://graph.facebook.com' }).whatsappGraphBaseUrl).toThrow(ConfigError);
  });

  it('material TLS / kunci PII / access key MinIO dibaca langsung; env tidak pernah bertambah (SEC-29)', () => {
    const env: Record<string, string | undefined> = {
      APP_ENV: 'dev',
      PII_ENCRYPTION_KEY_FILE: '/pii',
      MINIO_ACCESS_KEY_FILE: '/ak',
      JWT_SECRET_FILE: '/jwt',
    };
    const before = Object.keys(env).sort();
    const cfg = new AppConfig(env, (p) =>
      Buffer.from(p === '/pii' ? Buffer.alloc(32, 1).toString('base64') : p === '/ak' ? 'efn-bff-access-key' : 'isi-jwt-0123456789abcdef'),
    );
    expect(cfg.piiKeyRing.get(1)?.length).toBe(32);
    expect(cfg.minioAccessKey).toBe('efn-bff-access-key');
    expect(cfg.jwtSecret).toBe('isi-jwt-0123456789abcdef');
    expect(Object.keys(env).sort()).toEqual(before);
  });
});

describe('AppConfig — kunci PII dari file (SEC-29)', () => {
  const k1 = Buffer.alloc(32, 7).toString('base64');
  const k2 = Buffer.alloc(32, 9).toString('base64');

  it('PII_ENCRYPTION_KEY_FILE / _V2_FILE dibaca langsung sebagai Buffer, buffer mentah di-nol-kan, hasil di-cache', () => {
    const raws: Buffer[] = [];
    const reads: string[] = [];
    const reader = (p: string): Buffer => {
      reads.push(p);
      const b = Buffer.from(`${p === '/s/pii' ? k1 : k2}\n`);
      raws.push(b);
      return b;
    };
    const env: Record<string, string> = {
      NODE_ENV: 'production',
      PII_ENCRYPTION_KEY_FILE: '/s/pii',
      PII_ENCRYPTION_KEY_V2_FILE: '/s/pii2',
      PII_ACTIVE_KEY_VERSION: '2',
    };
    const cfg = new AppConfig(env, reader);
    const ring = cfg.piiKeyRing;
    expect(ring.get(1)?.equals(Buffer.alloc(32, 7))).toBe(true);
    expect(ring.get(2)?.equals(Buffer.alloc(32, 9))).toBe(true);
    expect(cfg.piiActiveKeyVersion).toBe(2);
    expect(reads.sort()).toEqual(['/s/pii', '/s/pii2']); // sekali per file
    for (const r of raws) expect(r.every((b) => b === 0)).toBe(true);
    expect(env.PII_ENCRYPTION_KEY).toBeUndefined();
  });

  it('fallback /run/secrets/pii_encryption_key (SECRETS_DIR) untuk v1; tidak ada → error jelas', () => {
    const cfg = new AppConfig({ NODE_ENV: 'production', SECRETS_DIR: '/sec' }, (p) => {
      if (p === '/sec/pii_encryption_key') return Buffer.from(k1);
      throw new Error('ENOENT');
    });
    expect(cfg.piiKeyRing.get(1)?.length).toBe(32);
    expect(() => new AppConfig({ NODE_ENV: 'production' }, () => Buffer.alloc(0)).piiKeyRing).toThrow('harus base64');
    expect(
      () =>
        new AppConfig({ NODE_ENV: 'production' }, () => {
          throw new Error('ENOENT');
        }).piiKeyRing,
    ).toThrow('PII_ENCRYPTION_KEY (v1) belum dikonfigurasi');
  });

  it('literal env PII_ENCRYPTION_KEY hanya APP_ENV=dev; file tak terbaca → error tanpa nilai', () => {
    expect(() => new AppConfig({ NODE_ENV: 'production', PII_ENCRYPTION_KEY: k1 }).piiKeyRing).toThrow('ditolak di luar APP_ENV=dev');
    expect(() => new AppConfig({ NODE_ENV: 'development', APP_ENV: 'production', PII_ENCRYPTION_KEY: k1 }).piiKeyRing).toThrow(
      'ditolak di luar APP_ENV=dev',
    );
    expect(testConfig({ PII_ENCRYPTION_KEY: k1 }).piiKeyRing.get(1)?.equals(Buffer.alloc(32, 7))).toBe(true);
    let msg = '';
    try {
      void new AppConfig({ NODE_ENV: 'production', PII_ENCRYPTION_KEY_FILE: '/hilang' }, () => {
        throw new Error('ENOENT');
      }).piiKeyRing;
    } catch (err) {
      msg = (err as Error).message;
    }
    expect(msg).toContain('PII_ENCRYPTION_KEY_FILE (/hilang) tidak dapat dibaca');
  });
});

describe('AppConfig — access key MinIO dari file (docs/04 §8.12/§8.13)', () => {
  const AK = 'Q3vX9pLm2Rt8Kw4Zb7Nc';
  const base = { MINIO_SECRET_KEY: 's'.repeat(32), MINIO_USE_SSL: 'false', EFN_ALLOW_PLAINTEXT: 'true' };

  it('MINIO_ACCESS_KEY_FILE dibaca (trim), menang atas literal', () => {
    const cfg = testConfig({ ...base, MINIO_ACCESS_KEY_FILE: '/run/secrets/minio_bff_access_key', MINIO_ACCESS_KEY: 'efn-bff' }, (p) =>
      p === '/run/secrets/minio_bff_access_key' ? Buffer.from(`${AK}\n`) : Buffer.alloc(0),
    );
    expect(cfg.minioConnection.accessKey).toBe(AK);
  });

  it('fallback /run/secrets/minio_bff_access_key tanpa env', () => {
    const cfg = testConfig({ ...base, SECRETS_DIR: '/sec' }, (p) => {
      if (p === '/sec/minio_bff_access_key') return Buffer.from(AK);
      throw new Error('ENOENT');
    });
    expect(cfg.minioAccessKey).toBe(AK);
  });

  it('literal MINIO_ACCESS_KEY hanya APP_ENV=dev; di luar dev ditolak; file tak terbaca/kosong/pendek/karakter aneh → ConfigError', () => {
    expect(testConfig({ ...base, APP_ENV: 'dev', MINIO_ACCESS_KEY: 'efn-bff' }).minioAccessKey).toBe('efn-bff');
    expect(() => testConfig({ ...base, MINIO_ACCESS_KEY: 'efn-bff' }).minioAccessKey).toThrow('hanya untuk APP_ENV=dev');
    expect(() => testConfig({ ...base, APP_ENV: 'staging', MINIO_ACCESS_KEY: AK }).minioAccessKey).toThrow('hanya untuk APP_ENV=dev');
    expect(() => testConfig({ ...base, MINIO_ACCESS_KEY_FILE: '/x' }).minioAccessKey).toThrow('MINIO_ACCESS_KEY_FILE (/x) tidak dapat dibaca');
    expect(() => testConfig({ ...base }).minioAccessKey).toThrow('belum dikonfigurasi');
    expect(() => testConfig({ ...base, MINIO_ACCESS_KEY_FILE: '/x' }, () => Buffer.from('efn-bff')).minioAccessKey).toThrow('terlalu pendek');
    expect(() => testConfig({ ...base, MINIO_ACCESS_KEY_FILE: '/x' }, () => Buffer.from(`${AK} spasi`)).minioAccessKey).toThrow(
      'tidak valid',
    );
    expect(testConfig({ ...base, APP_ENV: 'dev', MINIO_ACCESS_KEY_FILE: '/x' }, () => Buffer.from('efn-bff')).minioAccessKey).toBe('efn-bff');
  });
});

describe('AppConfig — ALTCHA & receipt DSAR', () => {
  it('SEC-34: default ALTCHA_MAX_NUMBER 500 000, override tetap dibatasi 1000..10 000 000', () => {
    expect(testConfig().altchaMaxNumber).toBe(500_000);
    expect(testConfig({ ALTCHA_MAX_NUMBER: '1000000' }).altchaMaxNumber).toBe(1_000_000);
    expect(() => testConfig({ ALTCHA_MAX_NUMBER: '20000000' }).altchaMaxNumber).toThrow(ConfigError);
  });

  it('SEC-34: ALTCHA_MAX_NUMBER_CAP default max(4 000 000, base), harus ≥ base; ALTCHA_ADAPTIVE default aktif', () => {
    expect(testConfig().altchaMaxNumberCap).toBe(4_000_000);
    expect(testConfig({ ALTCHA_MAX_NUMBER: '6000000' }).altchaMaxNumberCap).toBe(6_000_000);
    expect(testConfig({ ALTCHA_MAX_NUMBER_CAP: '2000000' }).altchaMaxNumberCap).toBe(2_000_000);
    expect(() => testConfig({ ALTCHA_MAX_NUMBER_CAP: '100000' }).altchaMaxNumberCap).toThrow('harus ≥ ALTCHA_MAX_NUMBER');
    expect(() => testConfig({ ALTCHA_MAX_NUMBER_CAP: '20000000' }).altchaMaxNumberCap).toThrow(ConfigError);
    expect(testConfig().altchaAdaptive).toBe(true);
    expect(testConfig({ ALTCHA_ADAPTIVE: 'false' }).altchaAdaptive).toBe(false);
  });

  it('R6: kunci receipt = HKDF(jwt_secret, efn:dsar_receipt:v1), TTL default 30 hari', () => {
    const cfg = testConfig();
    const expected = Buffer.from(hkdfSync('sha256', cfg.jwtSecret, Buffer.alloc(0), 'efn:dsar_receipt:v1', 32));
    expect(cfg.dsarReceiptKey.equals(expected)).toBe(true);
    expect(cfg.dsarReceiptTtlDays).toBe(30);
    expect(() => testConfig({ DSAR_RECEIPT_TTL_DAYS: '365' }).dsarReceiptTtlDays).toThrow(ConfigError);
  });
});
