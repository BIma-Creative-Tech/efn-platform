# shellcheck shell=bash
# Validasi konfigurasi deploy NON-RAHASIA (SEC-50, gelombang 13). Di-source oleh:
#   - scripts/host/efn-deploy  (dari rilis yang SUDAH terverifikasi tanda tangannya, sebelum apa pun ditulis ke /opt/efn)
#   - scripts/deploy.sh        (lapis kedua + jalur manual Superadmin `sudo -E bash deploy.sh`)
#   - scripts/host/test-efn-deploy.sh (uji unit fungsi di bawah)
# Tidak menjalankan apa pun saat di-source; hanya mendefinisikan fungsi + daftar nama. Fungsi mengembalikan non-0
# dan mencetak alasan ke stderr (pemanggil yang memutuskan `die`).
#
# Asal nilai (SEC-50, docs/runbooks/key-custody.md §4c):
#   EFN_HOST_PINNED_VARS  -> /etc/efn/deploy.conf milik root (topologi host). DITOLAK dari env stdin efn-deploy.
#   EFN_RELEASE_CONFIG_VARS -> objek `config` di manifest efn-release.json (DI DALAM arsip bertanda tangan cosign).
#                            DITOLAK dari env stdin efn-deploy.
#   Env stdin efn-deploy hanya: GHCR_USER, GHCR_TOKEN, EFN_PKI_* dan nama di scripts/secrets.list (secret).

# Topologi host (pemegang kunci SSH deploy tidak boleh bisa membuka erp./bi./ops. ke internet, SEC-50a)
EFN_HOST_PINNED_VARS=(EFN_PUBLIC_BIND EFN_WG_BIND EFN_WG_HTTPS_PORT EFN_WG_OUTLET_CIDR EFN_WG_STAFF_CIDR
  TRAEFIK_DOMAIN COMPOSE_PROFILES EFN_ALLOW_CONTROL_OFF EFN_ALLOW_FEATURE_ON)
# Konfigurasi aplikasi non-rahasia, ditandatangani bersama rilis (SEC-50b/c)
EFN_RELEASE_CONFIG_VARS=(ACME_EMAIL PAYMENT_PROVIDER PG_SUPERUSER WHATSAPP_ENABLED WHATSAPP_PHONE_NUMBER_ID
  WHATSAPP_OTP_TEMPLATE ACME_DNS_PROVIDER ACME_DNS_TOKEN_ENV ACME_SERVER RABBITMQ_STRICT_PERMISSIONS
  MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS CONSENT_LOCATION_VERSION CONSENT_LOCATION_TEXT_SHA256
  CONSENT_MARKETING_VERSION CONSENT_MARKETING_TEXT_SHA256 DSAR_DELETE_GRACE_DAYS ETL_ENABLE_ODOO ETL_ENABLE_GL
  ETL_AUDIT_COPY_ENABLED OLLAMA_VISION_MODEL OCR_LLM_PROVIDER OCR_LLM_CROSS_BORDER_OK NINEROUTER_VISION_MODEL TEXT2SQL_ENABLED TEXT2SQL_LLM_PROVIDER
  TEXT2SQL_CROSS_BORDER_OK TEXT2SQL_OLLAMA_MODEL TEXT2SQL_NINEROUTER_MODEL TEXT2SQL_AGENT_ENABLED
  TEXT2SQL_AGENT_SUMMARY_PROVIDER TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK MIGRATION_ENABLED METABASE_ADMIN_EMAIL ETL_LOYALTY_RECON_NOT_BEFORE
  ONLINE_ENABLED ONLINE_CATALOG_ENABLED ONLINE_CHANNELS ONLINE_FULFILMENTS ONLINE_PAYMENT_TIMEOUT_SECONDS
  ONLINE_QUOTE_TTL_SECONDS ONLINE_MAX_OPEN_ORDERS_PER_MEMBER ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER ONLINE_PAYMENT_METHODS
  ONLINE_TRANSFER_MAX_UPLOADS ONLINE_TRANSFER_MIN_VERIFY_SECONDS ONLINE_MAX_OPEN_TRANSFER_ORDERS_PER_MEMBER
  ONLINE_CATALOG_SYNC_SECONDS ONLINE_PICKUP_CODE_MAX_DAYS
  ONLINE_ORDER_MEMBER_LINK_DAYS ONLINE_EVENT_RETENTION_DAYS ONLINE_ADMIN_AUDIT_RETENTION_DAYS ODOO_RPC_TIMEOUT_MS ODOO_RPC_MAXCONN
  ODOO_RPC_PUBLIC_MAX_CONCURRENCY ODOO_RPC_SAGA_MAX_CONCURRENCY
  ERASURE_SUPPRESSION_DAYS DSAR_OPEN_ORDER_MAX_DEFER_DAYS MEMBER_MIN_AGE PRICE_HUNTER_GEO_RETENTION_DAYS
  CH_PSEUDONYM_RETENTION_YEARS CH_GEO_RETENTION_DAYS MB_AUDIT_MAX_RETENTION_DAYS
  LOYALTY_EARN_EXCLUDE_POINT_PAYMENT ETL_LOYALTY_POINTS_MODE LOYALTY_POINT_VALUE_IDR LOYALTY_RESTORE_TOLERANCE_PCT
  LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS ETL_AUDIT_EPOCH_ANCHORS
  EFN_ODOO_PII_ENABLED EFN_ODOO_PII_REQUIRED
  FNB_QR_ENABLED FNB_QR_COMPANIES FNB_QR_BASE_URL_PB FNB_QR_BASE_URL_BCF FNB_QR_POINTS_ENABLED
  FNB_TABLE_SESSION_TTL_SECONDS FNB_MAX_SUBMISSIONS_PER_SESSION FNB_MAX_ITEMS_PER_SESSION FNB_MAX_LINES FNB_MAX_LINE_QTY
  FNB_CAPTCHA_MODE FNB_MENU_SYNC_SECONDS FNB_SESSION_RETENTION_DAYS FNB_SESSION_MEMBER_LINK_DAYS
  ONLINE_LOYALTY_REDEEM_ENABLED ONLINE_LOYALTY_EARN_ENABLED
  LOYALTY_EXPIRY_ENABLED LOYALTY_EXPIRY_DRY_RUN LOYALTY_INACTIVITY_EXPIRY_MONTHS LOYALTY_EXPIRY_NOTICE_DAYS
  LOYALTY_EXPIRY_RUN_HOUR_WIB LOYALTY_EXPIRY_NOTICE_HOUR_WIB LOYALTY_EXPIRY_BATCH_SIZE LOYALTY_EXPIRY_MAX_PER_RUN
  LOYALTY_ACTIVITY_EPOCH LOYALTY_POINT_MAX_AGE_DAYS LOYALTY_INACTIVE_ANONYMIZE LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS
  LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT
  ETL_PARTNER_REGION_ENABLED ETL_LOYALTY_RECON_ONLINE_ENABLED ETL_ENABLE_KDS ETL_KDS_ONLINE_ENABLED ETL_ENABLE_WASTE
  BOOKING_ENABLED BOOKING_COMPANIES BOOKING_MAX_ACTIVE_PER_MEMBER BOOKING_MEMBER_LINK_DAYS BOOKING_RETENTION_DAYS
  ONLINE_SLOTS_ENABLED
  PROMO_ENABLED PROMO_PUSH_ENABLED PROMO_COMPANIES PROMO_SYNC_SECONDS
  MGM_ENABLED MGM_CLAIM_WINDOW_DAYS MGM_SETTLE_INTERVAL_MS
  CONSENT_PRIVACY_VERSION CONSENT_PRIVACY_TEXT_SHA256 PRIVACY_CONSENT_ENFORCED)
# G22 (docs/04 §14.D.1, L-72 D-22-18): env yang DIHAPUS kontrak. Bukan konfigurasi rilis (kunci manifest tak dikenal =
# ditolak efn-deploy); jalur manual `sudo -E bash deploy.sh` -> production DITOLAK, staging peringatan (BFF: gagal start
# di production, warning di luar production).
EFN_REMOVED_CONFIG_VARS=(LOYALTY_POINT_EXPIRY_DAYS)
# G22 (docs/04 §14.7 L-73): fitur yang menunggu persetujuan klien (Legal/DPO/Meta). Di production nilai `true` hanya
# diterima bila nama variabel dicantumkan Superadmin di EFN_ALLOW_FEATURE_ON (/etc/efn/deploy.conf, dipin host) -
# manifest rilis bertanda tangan saja TIDAK cukup (pemegang akses CI tidak bisa menyalakan hangus/anonimisasi massal).
# SEC-175 (re-review r15): poin QR (pemblokir SEC-164..166/169/174, L-82 b) dan redeem poin online (SEC-170, G23) juga
# hanya bisa dinyalakan di production lewat izin Superadmin, bukan variabel CI/manifest saja.
EFN_GATED_FEATURES=(LOYALTY_EXPIRY_ENABLED LOYALTY_INACTIVE_ANONYMIZE LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT
  FNB_QR_POINTS_ENABLED ONLINE_LOYALTY_REDEEM_ENABLED)
# Subset EFN_GATED_FEATURES yang nilai berisikonya `false` (default true = filter perlindungan aktif). SEC-171 (r15):
# melepas filter consent `marketing` untuk notice hangus = keputusan Legal yang sama jenisnya dengan L-73, jadi memakai
# izin yang sama (EFN_ALLOW_FEATURE_ON), bukan izin darurat EFN_ALLOW_CONTROL_OFF.
EFN_GATED_FEATURES_OFF=(LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT)
# G22 (docs/04 §14.A.3/§14.A.8): PT F&B yang boleh menerima QR meja (company_code CHECK PB,BCF). API key QR per PT:
# scripts/secrets.list odoo_api_key_qr_<pt>, overlay infra/compose/docker-compose.online-qr-<pt>.yml (scripts/deploy.sh),
# efn-rotate-online-key --channel=QR-<PT>.
EFN_FNB_QR_COMPANY_CODES=(PB BCF)
# G23 (docs/04 §15.3, L-85): PT F&B yang boleh menerima reservasi meja. API key reservasi per PT: scripts/secrets.list
# odoo_api_key_booking_<pt>, overlay infra/compose/docker-compose.online-booking-<pt>.yml (scripts/deploy.sh),
# efn-rotate-online-key --channel=BOOKING-<PT>.
EFN_BOOKING_COMPANY_CODES=(PB BCF)
# G27 (docs/04 §18.1-§18.2): PT yang feed promonya ditarik BFF. API key promo per PT: scripts/secrets.list
# odoo_api_key_promo_<pt>, overlay infra/compose/docker-compose.online-promo-<pt>.yml (scripts/deploy.sh),
# efn-rotate-online-key --channel=PROMO-<PT>. PT lain = tambah di sini + secrets.list + compose + overlay + ALLOWED_CHANNELS.
EFN_PROMO_COMPANY_CODES=(PB)
# G21 AC-13 (docs/04 §13.2, ADR-036 §0): kunci PII Odoo produksi (scripts/secrets.list host:disabled [SA]) - di-mount ke
# odoo oleh infra/compose/docker-compose.pii.yml hanya bila EFN_ODOO_PII_ENABLED=true (scripts/deploy.sh).
EFN_ODOO_PII_KEY_NAMES=(odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key)
# G17 (docs/04 §10.13): saklar KHUSUS dev/test. Bukan konfigurasi rilis (kunci manifest tidak dikenal = ditolak
# efn-deploy); efn_validate_config juga menolak nilai true di staging/production lewat jalur manual deploy.sh.
EFN_DEV_ONLY_SWITCHES=(ODOO_ONLINE_MOCK EFN_PAYMENT_SIMULATOR ETL_AUDIT_EPOCH_AUTO ONLINE_COUNTER_LEGACY)
# Kontrol keamanan yang di production hanya boleh `false` bila nama-nya ada di EFN_ALLOW_CONTROL_OFF (deploy.conf)
# (ETL_LOYALTY_RECON_NOT_BEFORE: bukan boolean; izin = boleh MEMAJUKAN cutoff di production, dicek scripts/deploy.sh)
EFN_SECURITY_CONTROLS=(RABBITMQ_STRICT_PERMISSIONS ETL_AUDIT_COPY_ENABLED ETL_LOYALTY_RECON_NOT_BEFORE)
# G20 (docs/04 §12.1, L-48): kode kanal online = kode PT. Satu-satunya daftar yang diterima ONLINE_CHANNELS; GL di-mount
# compose base, sisanya lewat infra/compose/docker-compose.online-<kode huruf kecil>.yml (scripts/deploy.sh). Kode lama
# RTL/FNB dan kanal G19 berhifen DITOLAK. Kanal baru = tambah di sini + scripts/secrets.list + compose + overlay +
# ALLOWED_CHANNELS di scripts/host/efn-rotate-online-key (runbook odoo-online-api-key.md#kanal-baru).
# G24 (docs/04 §16.1, L-91..L-93): + WTZ (PT Wetzel's Pretzels; kios F&B, tidak di daftar QR/booking).
EFN_ONLINE_CHANNEL_CODES=(GL PB BCR BCF WTZ)
EFN_ONLINE_BASE_CHANNEL=GL
# Profil compose yang dikenal; production wajib memuat core,data,observability (alert/Loki/salinan audit)
EFN_KNOWN_PROFILES=(core data ai observability ops)
EFN_REQUIRED_PROD_PROFILES=(core data observability)
# Subnet WireGuard hub (infra/wireguard: outlet 10.88.0.0/16, staf 10.89.0.0/16)
EFN_WG_SUPERNET=10.88.0.0/15

_efn_err() { echo "validasi: $*" >&2; return 1; }

# Nilai tidak boleh memuat karakter kontrol (newline, CR, tab, ESC, DEL, ...): mencegah injeksi baris ke .env (SEC-50b)
efn_no_cntrl() { # nama nilai
  local LC_ALL=C
  [[ "$2" != *[[:cntrl:]]* ]] || _efn_err "$1 memuat karakter kontrol/multi-baris (ditolak)"
}

# IPv4 kanonik: 4 oktet 0-255 tanpa nol di depan (hindari tafsir oktal/ambigu)
efn_is_ipv4() {
  [[ "$1" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]] || return 1
  local i
  for i in 1 2 3 4; do (( BASH_REMATCH[i] <= 255 )) || return 1; done
}
efn_ip2int() {
  local a b c d
  IFS=. read -r a b c d <<< "$1"
  echo $(( (a << 24) | (b << 16) | (c << 8) | d ))
}
# CIDR kanonik: alamat jaringan (bit host nol), prefix 0-32
efn_is_cidr() {
  [[ "$1" == */* ]] || return 1
  local ip="${1%/*}" p="${1#*/}"
  efn_is_ipv4 "$ip" || return 1
  [[ "$p" =~ ^(0|[1-9][0-9]?)$ ]] && (( p <= 32 )) || return 1
  local mask=$(( p == 0 ? 0 : (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF ))
  (( ($(efn_ip2int "$ip") & mask) == $(efn_ip2int "$ip") ))
}
efn_ip_in_cidr() { # ip cidr
  local net="${2%/*}" p="${2#*/}"
  local mask=$(( p == 0 ? 0 : (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF ))
  (( ($(efn_ip2int "$1") & mask) == $(efn_ip2int "$net") ))
}
efn_cidr_within() { # inner outer
  (( ${1#*/} >= ${2#*/} )) && efn_ip_in_cidr "${1%/*}" "$2"
}

efn_list_has() { # nilai elemen...
  local x="$1"; shift
  local e
  for e in "$@"; do [[ "$e" == "$x" ]] && return 0; done
  return 1
}

# ── Topologi host (deploy.conf) ────────────────────────────────────────────────────────────────────────────────
# Mengisi default yang sama dengan deploy.sh lama lalu memvalidasi. Variabel global diubah (default).
efn_validate_topology() { # app_env
  local app_env="$1" v
  EFN_WG_BIND="${EFN_WG_BIND:-10.88.0.1}"
  EFN_WG_HTTPS_PORT="${EFN_WG_HTTPS_PORT:-443}"
  EFN_WG_OUTLET_CIDR="${EFN_WG_OUTLET_CIDR:-10.88.0.0/16}"
  EFN_WG_STAFF_CIDR="${EFN_WG_STAFF_CIDR:-10.89.0.0/16}"
  COMPOSE_PROFILES="${COMPOSE_PROFILES:-core,data,ai,observability,ops}"
  EFN_ALLOW_CONTROL_OFF="${EFN_ALLOW_CONTROL_OFF:-}"
  EFN_ALLOW_FEATURE_ON="${EFN_ALLOW_FEATURE_ON:-}"
  for v in "${EFN_HOST_PINNED_VARS[@]}"; do efn_no_cntrl "$v" "${!v:-}" || return 1; done

  [[ "${TRAEFIK_DOMAIN:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] \
    || _efn_err "TRAEFIK_DOMAIN tidak valid (huruf kecil, mis. efn.example)" || return 1

  efn_is_ipv4 "${EFN_PUBLIC_BIND:-}" || _efn_err "EFN_PUBLIC_BIND wajib IPv4 (IP host untuk 80/443 websecure)" || return 1
  [[ "$EFN_PUBLIC_BIND" != 0.0.0.0 ]] && ! efn_ip_in_cidr "$EFN_PUBLIC_BIND" 127.0.0.0/8 \
    || _efn_err "EFN_PUBLIC_BIND tidak boleh 0.0.0.0/loopback" || return 1
  ! efn_ip_in_cidr "$EFN_PUBLIC_BIND" "$EFN_WG_SUPERNET" \
    || _efn_err "EFN_PUBLIC_BIND tidak boleh di subnet WireGuard ${EFN_WG_SUPERNET}" || return 1

  # SEC-50a: entrypoint wg-https (erp./bi./ops.) HANYA di IP WireGuard hub - bukan IP publik/0.0.0.0
  efn_is_ipv4 "$EFN_WG_BIND" || _efn_err "EFN_WG_BIND bukan IPv4" || return 1
  efn_ip_in_cidr "$EFN_WG_BIND" 10.88.0.0/16 || efn_ip_in_cidr "$EFN_WG_BIND" 10.89.0.0/16 \
    || _efn_err "EFN_WG_BIND ${EFN_WG_BIND} harus IP WireGuard di 10.88.0.0/16 atau 10.89.0.0/16" || return 1
  [[ "$EFN_PUBLIC_BIND" != "$EFN_WG_BIND" ]] || _efn_err "EFN_PUBLIC_BIND dan EFN_WG_BIND harus berbeda" || return 1

  [[ "$EFN_WG_HTTPS_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( EFN_WG_HTTPS_PORT <= 65535 )) \
    || _efn_err "EFN_WG_HTTPS_PORT tidak valid" || return 1

  # SEC-50a: allowlist sumber wg-https hanya subnet WireGuard (bukan 0.0.0.0/0 / RFC1918 lebar)
  for v in EFN_WG_OUTLET_CIDR EFN_WG_STAFF_CIDR; do
    efn_is_cidr "${!v}" || _efn_err "$v bukan CIDR kanonik (alamat jaringan/prefix)" || return 1
    efn_cidr_within "${!v}" "$EFN_WG_SUPERNET" && (( ${!v#*/} >= 16 )) \
      || _efn_err "$v=${!v} harus di dalam ${EFN_WG_SUPERNET} dengan prefix >= /16 (bukan 0.0.0.0/0 / terlalu lebar)" || return 1
  done

  [[ "$COMPOSE_PROFILES" =~ ^[a-z]+(,[a-z]+)*$ ]] || _efn_err "COMPOSE_PROFILES tidak valid" || return 1
  local -a profs
  IFS=, read -r -a profs <<< "$COMPOSE_PROFILES"
  for v in "${profs[@]}"; do
    efn_list_has "$v" "${EFN_KNOWN_PROFILES[@]}" || _efn_err "profil compose tidak dikenal: $v" || return 1
  done
  if [[ "$app_env" == production ]]; then
    for v in "${EFN_REQUIRED_PROD_PROFILES[@]}"; do
      efn_list_has "$v" "${profs[@]}" \
        || _efn_err "production wajib COMPOSE_PROFILES memuat ${EFN_REQUIRED_PROD_PROFILES[*]} (tidak ada: $v)" || return 1
    done
  fi
  local -a allow
  read -r -a allow <<< "$EFN_ALLOW_CONTROL_OFF"
  for v in "${allow[@]}"; do
    efn_list_has "$v" "${EFN_SECURITY_CONTROLS[@]}" || _efn_err "EFN_ALLOW_CONTROL_OFF: nama tidak dikenal $v" || return 1
  done
  # G22 L-73: izin menyalakan fitur yang menunggu persetujuan klien (hanya nama di EFN_GATED_FEATURES)
  local -a feat
  read -r -a feat <<< "$EFN_ALLOW_FEATURE_ON"
  for v in "${feat[@]}"; do
    efn_list_has "$v" "${EFN_GATED_FEATURES[@]}" || _efn_err "EFN_ALLOW_FEATURE_ON: nama tidak dikenal $v" || return 1
  done
}

# G22 (docs/04 §14.A.9): daftar PT QR koma -> setiap kode WAJIB di EFN_FNB_QR_COMPANY_CODES dan unik. Dipakai
# efn_validate_config dan scripts/deploy.sh (overlay + cek API key QR). rc 1 + alasan di stderr.
efn_validate_fnb_qr_companies() { # daftar
  local raw="$1" c
  local -a list
  local -A seen=()
  [[ "$raw" =~ ^[A-Z]{2,8}(,[A-Z]{2,8}){0,3}$ ]] || _efn_err "FNB_QR_COMPANIES tidak valid (kode PT huruf besar dipisah koma)" || return 1
  IFS=, read -r -a list <<< "$raw"
  for c in "${list[@]}"; do
    efn_list_has "$c" "${EFN_FNB_QR_COMPANY_CODES[@]}" \
      || _efn_err "FNB_QR_COMPANIES: ${c} bukan PT F&B (${EFN_FNB_QR_COMPANY_CODES[*]}; docs/04 §14.A.4)" || return 1
    [[ -z "${seen[$c]:-}" ]] || _efn_err "FNB_QR_COMPANIES: ${c} ganda" || return 1
    seen[$c]=1
  done
}

# G27 (docs/04 §18.2): daftar PT promo koma -> setiap kode WAJIB di EFN_PROMO_COMPANY_CODES dan unik. Dipakai
# efn_validate_config dan scripts/deploy.sh (overlay + cek API key promo). rc 1 + alasan di stderr.
efn_validate_promo_companies() { # daftar
  local raw="$1" c
  local -A seen=()
  local -a list
  [[ "$raw" =~ ^[A-Z]{2,8}(,[A-Z]{2,8}){0,9}$ ]] || _efn_err "PROMO_COMPANIES tidak valid (kode PT huruf besar dipisah koma)" || return 1
  IFS=, read -r -a list <<< "$raw"
  for c in "${list[@]}"; do
    efn_list_has "$c" "${EFN_PROMO_COMPANY_CODES[@]}" \
      || _efn_err "PROMO_COMPANIES: ${c} belum punya user teknis/overlay promo (${EFN_PROMO_COMPANY_CODES[*]}; docs/04 §18.2)" || return 1
    [[ -z "${seen[$c]:-}" ]] || _efn_err "PROMO_COMPANIES: ${c} ganda" || return 1
    seen[$c]=1
  done
}

# G23 (docs/04 §15.3): daftar PT reservasi koma -> setiap kode WAJIB di EFN_BOOKING_COMPANY_CODES dan unik. Dipakai
# efn_validate_config dan scripts/deploy.sh (overlay + cek API key booking). rc 1 + alasan di stderr.
efn_validate_booking_companies() { # daftar
  local raw="$1" c
  local -a list
  local -A seen=()
  [[ "$raw" =~ ^[A-Z]{2,8}(,[A-Z]{2,8}){0,3}$ ]] || _efn_err "BOOKING_COMPANIES tidak valid (kode PT huruf besar dipisah koma)" || return 1
  IFS=, read -r -a list <<< "$raw"
  for c in "${list[@]}"; do
    efn_list_has "$c" "${EFN_BOOKING_COMPANY_CODES[@]}" \
      || _efn_err "BOOKING_COMPANIES: ${c} bukan PT F&B reservasi (${EFN_BOOKING_COMPANY_CODES[*]}; docs/04 §15.3)" || return 1
    [[ -z "${seen[$c]:-}" ]] || _efn_err "BOOKING_COMPANIES: ${c} ganda" || return 1
    seen[$c]=1
  done
}

# G20 (docs/04 §12.1): daftar kanal koma -> setiap kode WAJIB di EFN_ONLINE_CHANNEL_CODES dan unik. Dipakai
# efn_validate_config dan scripts/deploy.sh (overlay + cek API key). rc 1 + alasan di stderr.
efn_validate_online_channels() { # daftar
  local raw="$1" c
  local -a list
  local -A seen=()
  [[ "$raw" =~ ^[A-Z][A-Z0-9-]{1,15}(,[A-Z][A-Z0-9-]{1,15}){0,7}$ ]] || _efn_err "ONLINE_CHANNELS tidak valid" || return 1
  IFS=, read -r -a list <<< "$raw"
  for c in "${list[@]}"; do
    efn_list_has "$c" "${EFN_ONLINE_CHANNEL_CODES[@]}" \
      || _efn_err "ONLINE_CHANNELS: kanal ${c} tidak dikenal (G20: ${EFN_ONLINE_CHANNEL_CODES[*]}; RTL/FNB & kanal G19 dihapus, docs/04 §12.1)" \
      || return 1
    [[ -z "${seen[$c]:-}" ]] || _efn_err "ONLINE_CHANNELS: kanal ${c} ganda" || return 1
    seen[$c]=1
  done
}

# ── Konfigurasi rilis (manifest bertanda tangan / env manual Superadmin) ──────────────────────────────────────
# Nilai kosong = default deploy.sh. Regex per variabel; tidak ada nilai bebas.
efn_validate_config() { # app_env
  local app_env="$1" v
  for v in "${EFN_RELEASE_CONFIG_VARS[@]}"; do efn_no_cntrl "$v" "${!v:-}" || return 1; done
  _efn_re() { [[ -z "${!1:-}" || "${!1}" =~ $2 ]] || _efn_err "$1 tidak valid"; }
  [[ "${ACME_EMAIL:-}" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || _efn_err "ACME_EMAIL tidak valid" || return 1
  [[ "${PAYMENT_PROVIDER:-}" =~ ^(mock|disabled|xendit|midtrans)$ ]] \
    || _efn_err "PAYMENT_PROVIDER wajib eksplisit: mock|disabled|xendit|midtrans" || return 1
  [[ "$app_env" == production && "$PAYMENT_PROVIDER" == mock ]] \
    && { _efn_err "production menolak PAYMENT_PROVIDER=mock (docs/04 §8.3)"; return 1; }
  _efn_re PG_SUPERUSER '^[a-z_][a-z0-9_]{0,62}$' || return 1
  _efn_re WHATSAPP_PHONE_NUMBER_ID '^[0-9]{1,32}$' || return 1
  _efn_re WHATSAPP_OTP_TEMPLATE '^[a-z0-9_]{1,128}$' || return 1
  _efn_re ACME_DNS_PROVIDER '^[a-z0-9]{1,32}$' || return 1
  _efn_re ACME_DNS_TOKEN_ENV '^[A-Z][A-Z0-9_]{1,63}$' || return 1
  _efn_re ACME_SERVER '^https://acme-(staging-)?v02\.api\.letsencrypt\.org/directory$' || return 1
  _efn_re METABASE_ADMIN_EMAIL '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' || return 1
  _efn_re OLLAMA_VISION_MODEL '^[a-z0-9][a-z0-9._/-]{0,127}(:[a-z0-9._-]{1,64})?$' || return 1
  # G37 (K25-01, docs/04 §22): gateway LLM OCR. 9router = transfer lintas negara -> wajib OCR_LLM_CROSS_BORDER_OK=true;
  # production ditolak sampai DPO memutuskan (Q-T7, PDP_TRANSFER_LINTAS_NEGARA.md P6). Tidak bisa dilewati.
  _efn_re OCR_LLM_PROVIDER '^(ollama|ninerouter|ninerouter_ollama)$' || return 1
  _efn_re NINEROUTER_VISION_MODEL '^[a-z0-9][a-z0-9._/-]{0,127}$' || return 1
  if [[ "${OCR_LLM_PROVIDER:-ollama}" != ollama ]]; then
    [[ "${OCR_LLM_CROSS_BORDER_OK:-false}" == true ]] \
      || { _efn_err "OCR_LLM_PROVIDER=${OCR_LLM_PROVIDER} membutuhkan OCR_LLM_CROSS_BORDER_OK=true (DPO, PDP P6)"; return 1; }
    [[ "$app_env" != production ]] \
      || { _efn_err "production menolak OCR_LLM_PROVIDER=${OCR_LLM_PROVIDER} sampai DPO memutuskan transfer (Q-T7)"; return 1; }
  fi
  # G38 (docs/04 §23.6): Text-to-SQL. 9router mengirim teks pertanyaan staf ke luar negeri (PDP P7) -> sama dengan G37.
  _efn_re TEXT2SQL_LLM_PROVIDER '^(ollama|ninerouter|ninerouter_ollama)$' || return 1
  _efn_re TEXT2SQL_OLLAMA_MODEL '^[a-z0-9][a-z0-9._/-]{0,127}(:[a-z0-9._-]{1,64})?$' || return 1
  _efn_re TEXT2SQL_NINEROUTER_MODEL '^[a-z0-9][a-z0-9._/-]{0,127}$' || return 1
  if [[ "${TEXT2SQL_LLM_PROVIDER:-ollama}" != ollama ]]; then
    [[ "${TEXT2SQL_CROSS_BORDER_OK:-false}" == true ]] \
      || { _efn_err "TEXT2SQL_LLM_PROVIDER=${TEXT2SQL_LLM_PROVIDER} membutuhkan TEXT2SQL_CROSS_BORDER_OK=true (DPO, PDP P7)"; return 1; }
    [[ "$app_env" != production ]] \
      || { _efn_err "production menolak TEXT2SQL_LLM_PROVIDER=${TEXT2SQL_LLM_PROVIDER} sampai DPO memutuskan transfer (Q-T7)"; return 1; }
  fi
  # G39 (docs/04 §24.5): ringkasan Analisis bertahap via 9router mengirim ANGKA HASIL agregat ke luar negeri (PDP P8)
  # -> izin terpisah dari P7, butuh jalur 9router SQL (overlay egress + key), production ditolak (Q-T7).
  _efn_re TEXT2SQL_AGENT_SUMMARY_PROVIDER '^(none|ollama|ninerouter|ninerouter_ollama)$' || return 1
  if [[ "${TEXT2SQL_AGENT_SUMMARY_PROVIDER:-none}" == ninerouter* ]]; then
    [[ "${TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK:-false}" == true ]] \
      || { _efn_err "TEXT2SQL_AGENT_SUMMARY_PROVIDER=${TEXT2SQL_AGENT_SUMMARY_PROVIDER} membutuhkan TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK=true (DPO, PDP P8)"; return 1; }
    [[ "${TEXT2SQL_LLM_PROVIDER:-ollama}" != ollama ]] \
      || { _efn_err "TEXT2SQL_AGENT_SUMMARY_PROVIDER=${TEXT2SQL_AGENT_SUMMARY_PROVIDER} membutuhkan TEXT2SQL_LLM_PROVIDER 9router (overlay egress + key)"; return 1; }
    [[ "$app_env" != production ]] \
      || { _efn_err "production menolak TEXT2SQL_AGENT_SUMMARY_PROVIDER=${TEXT2SQL_AGENT_SUMMARY_PROVIDER} sampai DPO memutuskan transfer (Q-T7)"; return 1; }
  fi
  for v in MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS DSAR_DELETE_GRACE_DAYS; do
    _efn_re "$v" '^[0-9]{1,5}$' || return 1
  done
  for v in WHATSAPP_ENABLED RABBITMQ_STRICT_PERMISSIONS ETL_ENABLE_ODOO ETL_ENABLE_GL ETL_AUDIT_COPY_ENABLED MIGRATION_ENABLED \
           ONLINE_ENABLED ONLINE_CATALOG_ENABLED LOYALTY_EARN_EXCLUDE_POINT_PAYMENT EFN_ODOO_PII_ENABLED EFN_ODOO_PII_REQUIRED \
           ETL_PARTNER_REGION_ENABLED ETL_LOYALTY_RECON_ONLINE_ENABLED ETL_ENABLE_KDS ETL_KDS_ONLINE_ENABLED \
           ETL_ENABLE_WASTE OCR_LLM_CROSS_BORDER_OK TEXT2SQL_ENABLED TEXT2SQL_CROSS_BORDER_OK TEXT2SQL_AGENT_ENABLED \
           TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK; do
    _efn_re "$v" '^(true|false)$' || return 1
  done
  # G21 AC-13 (docs/04 §13.2): production dengan kunci PII Odoo ter-mount WAJIB mode terkunci eksplisit
  # (EFN_ODOO_PII_REQUIRED=true; kosong tidak cukup). Tanpa itu kunci yang hilang saat berjalan hanya WARNING dan Odoo
  # bisa menulis PII baru tanpa enkripsi yang terdeteksi (L-63). Tidak bisa dimatikan lewat EFN_ALLOW_CONTROL_OFF.
  if [[ "$app_env" == production && "${EFN_ODOO_PII_ENABLED:-false}" == true && "${EFN_ODOO_PII_REQUIRED:-}" != true ]]; then
    _efn_err "production EFN_ODOO_PII_ENABLED=true wajib EFN_ODOO_PII_REQUIRED=true (AC-13, docs/runbooks/key-custody.md)"
    return 1
  fi
  # SEC-148 (r13, QA G21): mode migrasi `efnpii1:` (EFN_ODOO_PII_ACCEPT_V1=true) hanya untuk data PoC dev/staging.
  # Bukan konfigurasi rilis (tidak ada di EFN_RELEASE_CONFIG_VARS, tidak diteruskan compose) - ini lapis kedua untuk
  # jalur manual `sudo -E bash deploy.sh`: production menolak nilai selain kosong/false. Tidak bisa dimatikan lewat
  # EFN_ALLOW_CONTROL_OFF.
  if [[ -n "${EFN_ODOO_PII_ACCEPT_V1:-}" ]]; then
    _efn_re EFN_ODOO_PII_ACCEPT_V1 '^(true|false)$' || return 1
    if [[ "$app_env" == production && "$EFN_ODOO_PII_ACCEPT_V1" == true ]]; then
      _efn_err "production menolak EFN_ODOO_PII_ACCEPT_V1=true (mode migrasi efnpii1, SEC-148)"
      return 1
    fi
  fi
  # G17 penjualan online (docs/04 §10.1/§10.13; angka PLACEHOLDER pilot). Kosong = default compose (online MATI).
  _efn_re ONLINE_CHANNELS '^[A-Z][A-Z0-9-]{1,15}(,[A-Z][A-Z0-9-]{1,15}){0,7}$' || return 1
  efn_validate_online_channels "${ONLINE_CHANNELS:-$EFN_ONLINE_BASE_CHANNEL}" || return 1
  _efn_re ONLINE_FULFILMENTS '^(pickup|delivery)(,(pickup|delivery))?$' || return 1
  _efn_re ETL_LOYALTY_POINTS_MODE '^(product|payment)$' || return 1
  # SEC-114 (L-25b): mode `product` mematikan rekonsiliasi harian K-18-6 dan membuat redeem sah menjadi anomali palsu
  # (Odoo G18 menolak produk poin). Hanya untuk data historis di staging/dev.
  if [[ "$app_env" == production && "${ETL_LOYALTY_POINTS_MODE:-payment}" == product ]]; then
    _efn_err "production menolak ETL_LOYALTY_POINTS_MODE=product (SEC-114; pakai payment)"; return 1
  fi
  # G21/G22 (QA G22 P2-3): prasyarat job ETL opsional = cermin services/etl/efn_etl/config.py (ETL gagal start bila
  # dilanggar -> tolak di sini agar terlihat sebelum deploy). ETL_ENABLE_ODOO kosong = aktif otomatis setelah grant
  # (scripts/deploy.sh) -> diterima; hanya `false` eksplisit yang bertentangan. Semua lingkungan (bukan gerbang prod).
  if [[ "${ETL_ENABLE_ODOO:-}" == false ]]; then
    for v in ETL_PARTNER_REGION_ENABLED ETL_ENABLE_KDS ETL_LOYALTY_RECON_ONLINE_ENABLED ETL_ENABLE_WASTE; do
      [[ "${!v:-false}" != true ]] || _efn_err "${v}=true membutuhkan ETL_ENABLE_ODOO (bukan false)" || return 1
    done
  fi
  if [[ "${ETL_KDS_ONLINE_ENABLED:-false}" == true && "${ETL_ENABLE_KDS:-false}" != true ]]; then
    _efn_err "ETL_KDS_ONLINE_ENABLED=true membutuhkan ETL_ENABLE_KDS=true"; return 1
  fi
  if [[ "${ETL_LOYALTY_RECON_ONLINE_ENABLED:-false}" == true && "${ETL_LOYALTY_POINTS_MODE:-payment}" != payment ]]; then
    _efn_err "ETL_LOYALTY_RECON_ONLINE_ENABLED=true membutuhkan ETL_LOYALTY_POINTS_MODE=payment"; return 1
  fi
  local spec name lo hi
  for spec in ONLINE_PAYMENT_TIMEOUT_SECONDS:60:3600 ONLINE_QUOTE_TTL_SECONDS:60:1800 \
              ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:1:20 ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:1:10 \
              ONLINE_TRANSFER_MAX_UPLOADS:1:5 ONLINE_TRANSFER_MIN_VERIFY_SECONDS:300:1800 \
              ONLINE_MAX_OPEN_TRANSFER_ORDERS_PER_MEMBER:1:3 \
              ONLINE_CATALOG_SYNC_SECONDS:60:86400 \
              ONLINE_PICKUP_CODE_MAX_DAYS:1:30 ONLINE_ORDER_MEMBER_LINK_DAYS:30:3650 \
              ONLINE_EVENT_RETENTION_DAYS:7:3650 ONLINE_ADMIN_AUDIT_RETENTION_DAYS:30:3650 ODOO_RPC_TIMEOUT_MS:1000:30000 ODOO_RPC_MAXCONN:1:16 \
              ODOO_RPC_PUBLIC_MAX_CONCURRENCY:1:16 ODOO_RPC_SAGA_MAX_CONCURRENCY:1:16 \
              DSAR_OPEN_ORDER_MAX_DEFER_DAYS:1:14 MEMBER_MIN_AGE:17:21 PRICE_HUNTER_GEO_RETENTION_DAYS:1:3650 \
              CH_MART_RETENTION_YEARS:1:30 CH_PSEUDONYM_RETENTION_YEARS:1:30 CH_GEO_RETENTION_DAYS:1:3650 \
              MB_AUDIT_MAX_RETENTION_DAYS:30:3650 LOYALTY_POINT_VALUE_IDR:1:1000000 ETL_AUDIT_EPOCH_ANCHORS:1:1000; do
    IFS=: read -r name lo hi <<< "$spec"
    [[ -z "${!name:-}" ]] && continue
    [[ "${!name}" =~ ^[1-9][0-9]{0,5}$ ]] && (( ${!name} >= lo && ${!name} <= hi )) \
      || _efn_err "$name harus bilangan bulat ${lo}..${hi}" || return 1
  done
  # r11 (SEC-118/124, L-25f/l): toleransi restore poin 0..50 % (0 sah: harus persis); retensi reservasi committed
  # = catatan keuangan, minimal 10 th (BFF menolak < 3650, plafon 7300)
  if [[ -n "${LOYALTY_RESTORE_TOLERANCE_PCT:-}" ]]; then
    [[ "$LOYALTY_RESTORE_TOLERANCE_PCT" =~ ^(0|[1-9][0-9]?)$ ]] && (( LOYALTY_RESTORE_TOLERANCE_PCT <= 50 )) \
      || _efn_err "LOYALTY_RESTORE_TOLERANCE_PCT harus bilangan bulat 0..50" || return 1
  fi
  if [[ -n "${LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS:-}" ]]; then
    [[ "$LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS" =~ ^[1-9][0-9]{3}$ ]] \
      && (( LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS >= 3650 && LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS <= 7300 )) \
      || _efn_err "LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS harus bilangan bulat 3650..7300" || return 1
  fi
  # W-19 (Q-P12, 10 Okt 2026): daftar tolak erasure 365 h (default BFF); 0 = tidak disimpan, plafon BFF 730
  if [[ -n "${ERASURE_SUPPRESSION_DAYS:-}" ]]; then
    [[ "$ERASURE_SUPPRESSION_DAYS" =~ ^(0|[1-9][0-9]{0,2})$ ]] && (( ERASURE_SUPPRESSION_DAYS <= 730 )) \
      || _efn_err "ERASURE_SUPPRESSION_DAYS harus bilangan bulat 0..730" || return 1
  fi
  # W-19 (data-AI, clickhouse-init): kolom pseudonim tidak boleh hidup lebih lama dari barisnya (kosong = 5 / 10)
  if (( ${CH_PSEUDONYM_RETENTION_YEARS:-5} > ${CH_MART_RETENTION_YEARS:-10} )); then
    _efn_err "CH_PSEUDONYM_RETENTION_YEARS (${CH_PSEUDONYM_RETENTION_YEARS:-5}) > CH_MART_RETENTION_YEARS (${CH_MART_RETENTION_YEARS:-10})"; return 1
  fi
  # SEC-96/SEC-109 (L-19f), sizing pilot opsi B (10 Okt 2026): pool json2 BFF (publik + saga) tidak boleh melebihi
  # koneksi gateway ke Odoo (= worker yang disisihkan untuk online). Kosong = default compose PROD (overlay
  # docker-compose.prod.yml: maxconn 2, pool 1 + 1) - deploy.sh selalu memakai overlay prod (staging juga).
  local eff_max="${ODOO_RPC_MAXCONN:-2}" eff_pub="${ODOO_RPC_PUBLIC_MAX_CONCURRENCY:-1}" eff_saga="${ODOO_RPC_SAGA_MAX_CONCURRENCY:-1}"
  if (( eff_pub + eff_saga > eff_max )); then
    _efn_err "ODOO_RPC_PUBLIC_MAX_CONCURRENCY (${eff_pub}) + ODOO_RPC_SAGA_MAX_CONCURRENCY (${eff_saga}) > ODOO_RPC_MAXCONN (${eff_max}) - worker Odoo POS bisa terpakai online (docs/runbooks/online-orders.md#sizing-worker)"
    return 1
  fi
  # G20 W-30 (docs/04 §12.2): order bayar-di-kasir adalah order terbuka juga -> batas counter <= batas total
  # (kosong = default compose 1 / 2).
  if (( ${ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:-1} > ${ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:-2} )); then
    _efn_err "ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER (${ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:-1}) > ONLINE_MAX_OPEN_ORDERS_PER_MEMBER (${ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:-2})"
    return 1
  fi
  # ONLINE_PAYMENT_METHODS (L-55 -> G26 L-95/L-103): daftar qris,bank_transfer tanpa duplikat. `counter` (bayar di kasir)
  # dibekukan untuk pesanan online: hanya dev/test dengan ONLINE_COUNTER_LEGACY=true (BFF juga menolak start).
  local pay_methods="${ONLINE_PAYMENT_METHODS:-qris,bank_transfer}"
  local pm pm_seen=","
  IFS=',' read -r -a _efn_pms <<< "$pay_methods"
  if [[ -z "$pay_methods" || "$pay_methods" == *,, || "$pay_methods" == ,* || "$pay_methods" == *, ]]; then
    _efn_err "ONLINE_PAYMENT_METHODS harus daftar qris|bank_transfer dipisah koma (bukan ${pay_methods})"; return 1
  fi
  for pm in "${_efn_pms[@]}"; do
    if [[ ! "$pm" =~ ^(qris|bank_transfer|counter)$ || "$pm_seen" == *",$pm,"* ]]; then
      _efn_err "ONLINE_PAYMENT_METHODS harus daftar qris|bank_transfer tanpa duplikat (bukan ${pay_methods})"; return 1
    fi
    pm_seen+="$pm,"
  done
  if [[ "$pm_seen" == *",counter,"* && ( "${ONLINE_COUNTER_LEGACY:-false}" != true || "$app_env" =~ ^(staging|production)$ ) ]]; then
    _efn_err "ONLINE_PAYMENT_METHODS memuat counter: bayar di kasir dibekukan untuk pesanan online (L-95)"; return 1
  fi
  # Online di production: qris butuh payment provider nyata (mock ditolak di atas). Pilot tanpa vendor (L-103)
  # boleh HANYA bila daftar tanpa qris (mis. bank_transfer) dan PAYMENT_PROVIDER=disabled.
  if [[ "$app_env" == production && "${ONLINE_ENABLED:-false}" == true && ! "${PAYMENT_PROVIDER}" =~ ^(xendit|midtrans)$ ]]; then
    if [[ "$pm_seen" == *",qris,"* || "${PAYMENT_PROVIDER}" != disabled ]]; then
      _efn_err "production ONLINE_ENABLED=true butuh PAYMENT_PROVIDER xendit|midtrans, atau ONLINE_PAYMENT_METHODS=bank_transfer + PAYMENT_PROVIDER=disabled (L-103)"
      return 1
    fi
  fi
  # Saklar dev/test (stub json2 BFF L-7, simulator bayar frontend) tidak boleh aktif di staging/production
  for v in "${EFN_DEV_ONLY_SWITCHES[@]}"; do
    efn_no_cntrl "$v" "${!v:-}" || return 1
    if [[ -n "${!v:-}" && "${!v}" != false ]]; then
      _efn_err "${v}=${!v} ditolak di ${app_env} (hanya dev/test)"; return 1
    fi
  done
  # G14: go-live loyalty POS untuk rekonsiliasi ledger poin vs pos.order (ETL). UTC kanonik saja (perbandingan
  # leksikal = kronologis). Masa depan ditolak: cutoff > sekarang = rekonsiliasi buta (kontrol detektif mati diam-diam).
  _efn_re ETL_LOYALTY_RECON_NOT_BEFORE \
    '^20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$' || return 1
  if [[ -n "${ETL_LOYALTY_RECON_NOT_BEFORE:-}" && "$ETL_LOYALTY_RECON_NOT_BEFORE" > "$(date -u +%Y-%m-%dT%H:%M:%SZ)" ]]; then
    _efn_err "ETL_LOYALTY_RECON_NOT_BEFORE di masa depan (rekonsiliasi poin akan buta)"; return 1
  fi
  local p ver sha
  for p in LOCATION MARKETING PRIVACY; do
    ver="CONSENT_${p}_VERSION"; sha="CONSENT_${p}_TEXT_SHA256"
    _efn_re "$ver" '^[A-Za-z0-9._@-]{1,32}$' || return 1
    _efn_re "$sha" '^[0-9a-f]{64}$' || return 1
    if [[ -n "${!ver:-}" && -z "${!sha:-}" ]] || [[ -z "${!ver:-}" && -n "${!sha:-}" ]]; then
      _efn_err "$ver dan $sha harus di-set berpasangan"; return 1
    fi
  done
  efn_validate_g22_config "$app_env" || return 1
  # SEC-50c: kontrol keamanan tidak bisa dimatikan di production kecuali izin eksplisit Superadmin di deploy.conf
  if [[ "$app_env" == production ]]; then
    local -a allow
    read -r -a allow <<< "${EFN_ALLOW_CONTROL_OFF:-}"
    for v in "${EFN_SECURITY_CONTROLS[@]}"; do
      if [[ "${!v:-}" == false ]] && ! efn_list_has "$v" "${allow[@]}"; then
        _efn_err "production menolak ${v}=false (kontrol keamanan; izin hanya lewat EFN_ALLOW_CONTROL_OFF di /etc/efn/deploy.conf)"
        return 1
      fi
    done
  fi
}

# ── G22 (docs/04 §14): QR meja (W-07), poin online (W-12), poin hangus tidak aktif (W-17) ─────────────────────────
# Dipanggil efn_validate_config (manifest rilis bertanda tangan / env manual Superadmin). Kosong = default compose
# (QR, poin online, job hangus, anonimisasi MATI). Cek secret (API key QR nyata) ada di scripts/deploy.sh (host).
efn_validate_g22_config() { # app_env
  local app_env="$1" v spec name lo hi c
  local url_re='^https://([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(/[A-Za-z0-9._~-]+)*$'
  local date_re='^20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'
  local num_re='^(0|[1-9][0-9]{0,5})$'
  # D-22-18 (L-72): env yang dihapus kontrak -> production ditolak, staging peringatan (nilai tidak diteruskan compose)
  for v in "${EFN_REMOVED_CONFIG_VARS[@]}"; do
    efn_no_cntrl "$v" "${!v:-}" || return 1
    [[ -n "${!v:-}" ]] || continue
    if [[ "$app_env" == production ]]; then
      _efn_err "production menolak ${v} (dihapus G22 D-22-18; pakai LOYALTY_POINT_MAX_AGE_DAYS, docs/04 §14.D.1)"
      return 1
    fi
    echo "validasi: PERINGATAN ${v} diset tetapi sudah dihapus (G22 D-22-18) - tidak dipakai; hapus dari konfigurasi" >&2
  done
  for v in FNB_QR_ENABLED FNB_QR_POINTS_ENABLED ONLINE_LOYALTY_REDEEM_ENABLED ONLINE_LOYALTY_EARN_ENABLED \
           LOYALTY_EXPIRY_ENABLED LOYALTY_EXPIRY_DRY_RUN LOYALTY_INACTIVE_ANONYMIZE LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT; do
    [[ -z "${!v:-}" || "${!v}" == true || "${!v}" == false ]] || _efn_err "$v harus true|false" || return 1
  done
  [[ -z "${FNB_CAPTCHA_MODE:-}" || "$FNB_CAPTCHA_MODE" == off || "$FNB_CAPTCHA_MODE" == first_submit \
     || "$FNB_CAPTCHA_MODE" == always ]] || _efn_err "FNB_CAPTCHA_MODE harus off|first_submit|always" || return 1
  # Rentang (kontrak §14.A.7/§14.D.8; batas keras Odoo E-27: 30 baris, qty 20 per baris; TTL token maks 4 jam)
  for spec in FNB_TABLE_SESSION_TTL_SECONDS:600:14400 FNB_MAX_SUBMISSIONS_PER_SESSION:1:100 \
              FNB_MAX_ITEMS_PER_SESSION:1:1000 FNB_MAX_LINES:1:30 FNB_MAX_LINE_QTY:1:20 FNB_MENU_SYNC_SECONDS:60:3600 \
              FNB_SESSION_RETENTION_DAYS:7:395 FNB_SESSION_MEMBER_LINK_DAYS:1:365 \
              LOYALTY_INACTIVITY_EXPIRY_MONTHS:6:36 LOYALTY_EXPIRY_NOTICE_DAYS:7:60 LOYALTY_EXPIRY_BATCH_SIZE:1:5000 \
              LOYALTY_EXPIRY_MAX_PER_RUN:1:999999 LOYALTY_EXPIRY_RUN_HOUR_WIB:0:23 LOYALTY_EXPIRY_NOTICE_HOUR_WIB:0:23 \
              LOYALTY_POINT_MAX_AGE_DAYS:0:3650 LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS:0:90; do
    IFS=: read -r name lo hi <<< "$spec"
    [[ -z "${!name:-}" ]] && continue
    [[ "${!name}" =~ $num_re ]] && (( ${!name} >= lo && ${!name} <= hi )) \
      || _efn_err "$name harus bilangan bulat ${lo}..${hi}" || return 1
  done
  # Tautan member sesi QR tidak boleh hidup lebih lama dari baris sesinya (§14.A.11 / §14.E.5)
  if (( ${FNB_SESSION_MEMBER_LINK_DAYS:-30} > ${FNB_SESSION_RETENTION_DAYS:-180} )); then
    _efn_err "FNB_SESSION_MEMBER_LINK_DAYS (${FNB_SESSION_MEMBER_LINK_DAYS:-30}) > FNB_SESSION_RETENTION_DAYS (${FNB_SESSION_RETENTION_DAYS:-180})"
    return 1
  fi
  # QR meja: PT F&B saja; URL tenant https dari env (API7 SSRF: qr_url tidak dari input); aktif = URL wajib (API8)
  local companies="${FNB_QR_COMPANIES:-PB,BCF}"
  efn_validate_fnb_qr_companies "$companies" || return 1
  for c in "${EFN_FNB_QR_COMPANY_CODES[@]}"; do
    v="FNB_QR_BASE_URL_${c}"
    [[ -z "${!v:-}" || "${!v}" =~ $url_re ]] \
      || _efn_err "$v harus https://<host>[/path] huruf kecil tanpa port/query/fragment (docs/04 §14.A.10)" || return 1
  done
  if [[ "${FNB_QR_ENABLED:-false}" == true ]]; then
    local -a qr_list
    IFS=, read -r -a qr_list <<< "$companies"
    for c in "${qr_list[@]}"; do
      v="FNB_QR_BASE_URL_${c}"
      [[ -n "${!v:-}" ]] || _efn_err "FNB_QR_ENABLED=true tetapi ${v} kosong (docs/04 §14.A.10 API8)" || return 1
    done
  fi
  # Poin hangus (§14.D.8): epoch go-live wajib saat job aktif; anonimisasi hanya setelah hangus (§14.D.7)
  [[ -z "${LOYALTY_ACTIVITY_EPOCH:-}" || "$LOYALTY_ACTIVITY_EPOCH" =~ $date_re ]] \
    || _efn_err "LOYALTY_ACTIVITY_EPOCH harus tanggal YYYY-MM-DD" || return 1
  if [[ "${LOYALTY_EXPIRY_ENABLED:-false}" == true && -z "${LOYALTY_ACTIVITY_EPOCH:-}" ]]; then
    _efn_err "LOYALTY_EXPIRY_ENABLED=true wajib LOYALTY_ACTIVITY_EPOCH (D-22-15, docs/04 §14.D.8)"; return 1
  fi
  if [[ "${LOYALTY_INACTIVE_ANONYMIZE:-false}" == true && "${LOYALTY_EXPIRY_ENABLED:-false}" != true ]]; then
    _efn_err "LOYALTY_INACTIVE_ANONYMIZE=true butuh LOYALTY_EXPIRY_ENABLED=true (anonimisasi setelah hangus, §14.D.7)"; return 1
  fi
  # G23 (docs/04 §15): kill switch reservasi & slot pre-order; angka = rentang yang sama dengan BFF app-config.ts
  for v in BOOKING_ENABLED ONLINE_SLOTS_ENABLED; do
    [[ -z "${!v:-}" || "${!v}" == true || "${!v}" == false ]] || _efn_err "$v harus true|false" || return 1
  done
  for spec in BOOKING_MAX_ACTIVE_PER_MEMBER:1:10 BOOKING_MEMBER_LINK_DAYS:1:400 BOOKING_RETENTION_DAYS:30:3650; do
    IFS=: read -r name lo hi <<< "$spec"
    [[ -z "${!name:-}" ]] && continue
    [[ "${!name}" =~ $num_re ]] && (( ${!name} >= lo && ${!name} <= hi )) \
      || _efn_err "$name harus bilangan bulat ${lo}..${hi}" || return 1
  done
  # Tautan member reservasi tidak boleh hidup lebih lama dari barisnya (§15.5 retensi)
  if (( ${BOOKING_MEMBER_LINK_DAYS:-180} > ${BOOKING_RETENTION_DAYS:-400} )); then
    _efn_err "BOOKING_MEMBER_LINK_DAYS (${BOOKING_MEMBER_LINK_DAYS:-180}) > BOOKING_RETENTION_DAYS (${BOOKING_RETENTION_DAYS:-400})"
    return 1
  fi
  efn_validate_booking_companies "${BOOKING_COMPANIES:-PB,BCF}" || return 1
  # G27 (docs/04 §18.2/§18.3a): promo Super-App + persetujuan Kebijakan Privasi. Rentang = BFF app-config.ts.
  for v in PROMO_ENABLED PROMO_PUSH_ENABLED PRIVACY_CONSENT_ENFORCED; do
    [[ -z "${!v:-}" || "${!v}" == true || "${!v}" == false ]] || _efn_err "$v harus true|false" || return 1
  done
  if [[ -n "${PROMO_SYNC_SECONDS:-}" ]]; then
    [[ "$PROMO_SYNC_SECONDS" =~ $num_re ]] && (( PROMO_SYNC_SECONDS >= 30 && PROMO_SYNC_SECONDS <= 3600 )) \
      || _efn_err "PROMO_SYNC_SECONDS harus bilangan bulat 30..3600" || return 1
  fi
  if [[ "${PROMO_PUSH_ENABLED:-false}" == true && "${PROMO_ENABLED:-false}" != true ]]; then
    _efn_err "PROMO_PUSH_ENABLED=true butuh PROMO_ENABLED=true (docs/04 §18.2)"; return 1
  fi
  # Default BFF = kanal online pertama; di rilis daftar promo wajib eksplisit saat promo aktif (overlay + API key per PT)
  if [[ "${PROMO_ENABLED:-false}" == true ]]; then
    [[ -n "${PROMO_COMPANIES:-}" ]] || _efn_err "PROMO_ENABLED=true wajib PROMO_COMPANIES (mis. PB)" || return 1
  fi
  if [[ -n "${PROMO_COMPANIES:-}" ]]; then efn_validate_promo_companies "$PROMO_COMPANIES" || return 1; fi
  # G31 (docs/04 §20.8): engine MGM. Rentang = BFF app-config.ts.
  [[ -z "${MGM_ENABLED:-}" || "$MGM_ENABLED" == true || "$MGM_ENABLED" == false ]] || _efn_err "MGM_ENABLED harus true|false" || return 1
  if [[ -n "${MGM_CLAIM_WINDOW_DAYS:-}" ]]; then
    [[ "$MGM_CLAIM_WINDOW_DAYS" =~ $num_re ]] && (( MGM_CLAIM_WINDOW_DAYS >= 1 && MGM_CLAIM_WINDOW_DAYS <= 365 )) \
      || _efn_err "MGM_CLAIM_WINDOW_DAYS harus bilangan bulat 1..365" || return 1
  fi
  if [[ -n "${MGM_SETTLE_INTERVAL_MS:-}" ]]; then
    [[ "$MGM_SETTLE_INTERVAL_MS" =~ $num_re ]] && (( MGM_SETTLE_INTERVAL_MS <= 86400000 )) \
      || _efn_err "MGM_SETTLE_INTERVAL_MS harus bilangan bulat 0..86400000" || return 1
  fi
  # Persetujuan privasi wajib = teks aktif harus ada (BFF menolak login tanpa versi aktif -> semua login gagal)
  if [[ "${PRIVACY_CONSENT_ENFORCED:-false}" == true && -z "${CONSENT_PRIVACY_VERSION:-}" ]]; then
    _efn_err "PRIVACY_CONSENT_ENFORCED=true wajib CONSENT_PRIVACY_VERSION + CONSENT_PRIVACY_TEXT_SHA256 (docs/04 §18.3a)"; return 1
  fi
  # L-73: job hangus & anonimisasi OFF di production kecuali izin Superadmin di deploy.conf (tidak bisa lewat manifest).
  # SEC-171: filter consent notice hangus (EFN_GATED_FEATURES_OFF) - nilai berisiko `false`, kosong = default compose true.
  if [[ "$app_env" == production ]]; then
    local -a feat_on
    local risky
    read -r -a feat_on <<< "${EFN_ALLOW_FEATURE_ON:-}"
    for v in "${EFN_GATED_FEATURES[@]}"; do
      risky=true
      if efn_list_has "$v" "${EFN_GATED_FEATURES_OFF[@]}"; then risky=false; fi
      if [[ "${!v:-}" == "$risky" ]] && ! efn_list_has "$v" "${feat_on[@]}"; then
        _efn_err "production menolak ${v}=${risky} (L-73/SEC-171: menunggu persetujuan Legal/DPO/Meta; izin hanya EFN_ALLOW_FEATURE_ON di /etc/efn/deploy.conf)"
        return 1
      fi
    done
  fi
}

# Baca kunci topologi yang dipin dari deploy.conf (root, tidak bisa ditulis group/other). Nilai env yang BERBEDA
# dari pin = ditolak (jalur manual tidak bisa diam-diam menimpa pin). Kunci lain di file diabaikan di sini.
efn_load_pinned_conf() { # file
  local f="$1" k v
  [[ -f "$f" && ! -L "$f" ]] || return 0
  [[ "$(stat -c '%u:%a' "$f")" =~ ^0:(6[04]4|600|640)$ ]] || _efn_err "$f harus milik root dan tidak bisa ditulis group/other" || return 1
  while IFS='=' read -r k v || [[ -n "$k" ]]; do
    efn_list_has "$k" "${EFN_HOST_PINNED_VARS[@]}" || continue
    v="${v#\'}"; v="${v%\'}"
    efn_no_cntrl "$k" "$v" || return 1
    if [[ -n "${!k:-}" && "${!k}" != "$v" ]]; then
      _efn_err "$k dari env (${!k}) berbeda dengan pin di $f ($v)"; return 1
    fi
    printf -v "$k" '%s' "$v"
    export "${k?}"
  done < "$f"
}

# Tulis .env "satu kunci satu baris": kunci unik, nilai tanpa karakter kontrol, jumlah baris diverifikasi ulang.
efn_write_dotenv() { # file KEY=val...
  local f="$1"; shift
  local tmp e k val
  local -A seen=()
  tmp=$(mktemp "${f}.XXXXXX")
  for e in "$@"; do
    k="${e%%=*}"; val="${e#*=}"
    [[ "$k" =~ ^[A-Z][A-Z0-9_]{0,63}$ ]] || { rm -f "$tmp"; _efn_err ".env: kunci tidak valid '$k'"; return 1; }
    [[ -z "${seen[$k]:-}" ]] || { rm -f "$tmp"; _efn_err ".env: kunci ganda $k"; return 1; }
    efn_no_cntrl "$k" "$val" || { rm -f "$tmp"; return 1; }
    seen[$k]=1
    printf '%s=%s\n' "$k" "$val" >> "$tmp"
  done
  local lines
  lines=$(wc -l < "$tmp")
  [[ "$lines" -eq $# ]] || { rm -f "$tmp"; _efn_err ".env: ${lines} baris != $# kunci"; return 1; }
  if grep -Evq '^[A-Z][A-Z0-9_]*=[^[:cntrl:]]*$' "$tmp"; then rm -f "$tmp"; _efn_err ".env: baris tidak valid"; return 1; fi
  chmod 0640 "$tmp"
  mv -f "$tmp" "$f"
}

# SEC-129 (r11): modul Odoo efn WAJIB berstatus `installed` di DB efn_odoo. Tanpa efn_pos_refund_origin kontrol E-31
# (refund ke metode asal) tidak aktif tanpa terdeteksi (SEC-122). Tambah modul wajib lain di sini.
EFN_REQUIRED_ODOO_MODULES=(efn_pos_refund_origin)
# stdin: baris `<nama>|<state>` hasil query ir_module_module. Cetak modul yang hilang/tidak `installed`
# (`<nama>=<state|absent>`); return 1 bila ada.
efn_odoo_modules_missing() {
  local line name state m bad=0
  declare -A st=()
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^([a-z0-9_]{1,64})\|([a-z\ ]{1,32})$ ]] || continue
    name="${BASH_REMATCH[1]}"; state="${BASH_REMATCH[2]}"
    st["$name"]="$state"
  done
  for m in "${EFN_REQUIRED_ODOO_MODULES[@]}"; do
    if [[ "${st[$m]:-absent}" != installed ]]; then echo "${m}=${st[$m]:-absent}"; bad=1; fi
  done
  return "$bad"
}

# G21 AC-13 (docs/04 §13.2, ADR-036 §0): ketiga kunci PII Odoo di <dir> harus ada sebagai file biasa (bukan symlink),
# berisi TEPAT 32 byte base64 kanonik (44 karakter, satu '=' penutup; newline akhir boleh), bukan placeholder `disabled`,
# dan ketiganya BERBEDA (satu kunci untuk dua peran = kebocoran satu membuka keduanya, SEC-140 c). Nilai kunci tidak
# pernah dicetak; alasan hanya menyebut nama file. rc 1 + alasan di stderr.
efn_validate_pii_keys() { # dir
  local dir="$1" n f v bad=0
  local -A seen=()
  for n in "${EFN_ODOO_PII_KEY_NAMES[@]}"; do
    f="${dir}/${n}.txt"
    if [[ -L "$f" || ! -f "$f" || ! -s "$f" ]]; then
      _efn_err "kunci PII ${n}: file ${n}.txt tidak ada/kosong/symlink (docs/runbooks/key-custody.md §4e)"; bad=1; continue
    fi
    v=$(tr -d '\n' < "$f")
    if [[ "$v" == disabled ]]; then
      _efn_err "kunci PII ${n}: masih placeholder 'disabled' (Superadmin belum membuat kunci produksi)"; bad=1; continue
    fi
    # 32 byte -> base64 44 karakter: 42 karakter bebas + 1 karakter dengan 2 bit terakhir nol (AEIMQUYcgkosw048) + '='
    if [[ ! "$v" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]] || [[ "$(wc -l < "$f")" -gt 1 ]]; then
      _efn_err "kunci PII ${n}: bukan 32 byte base64 kanonik (openssl rand -base64 32)"; bad=1; continue
    fi
    if [[ -n "${seen[$v]:-}" ]]; then
      _efn_err "kunci PII ${n}: sama dengan ${seen[$v]} (ketiga kunci wajib berbeda, ADR-036 §0.5)"; bad=1; continue
    fi
    seen[$v]="$n"
  done
  return "$bad"
}

# G21 AC-13: status modul efn_pii di DB efn_odoo vs saklar overlay. Modul terpasang (installed / to upgrade / to remove)
# tanpa EFN_ODOO_PII_ENABLED=true = Odoo berjalan TANPA kunci (data terlindungi tidak terbaca; ADR-036 mode terkunci)
# -> deploy gagal. `uninstalled`/`uninstallable`/`to install`/kosong (modul tidak dikenal DB) = boleh tanpa kunci.
efn_pii_module_state_ok() { # enabled state
  local enabled="$1" state="${2%$'\r'}"
  case "$state" in
    installed|"to upgrade"|"to remove")
      [[ "$enabled" == true ]] \
        || _efn_err "efn_pii berstatus '${state}' di efn_odoo tetapi EFN_ODOO_PII_ENABLED bukan true (kunci PII tidak di-mount)" ;;
    *) return 0 ;;
  esac
}
