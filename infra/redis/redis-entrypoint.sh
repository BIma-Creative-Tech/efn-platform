#!/bin/sh
# Entrypoint Redis EFN (docs/04 §7.1, §7.3, SEC-05). Dijalankan sebagai root oleh compose, lalu turun ke user
# `redis` lewat setpriv (seperti image resmi). TIDAK lewat docker-entrypoint.sh resmi (gelombang 7): entrypoint resmi menjalankan
# `find . ! -user redis -exec chown` sebagai root tanpa CAP_DAC_OVERRIDE -> "appendonlydir: Permission denied" dan
# container crash-loop pada SETIAP restart dengan volume berisi data. /data di image sudah milik redis.
#  - ACL dibangkitkan dari Docker secret ke tmpfs (/tmp, tidak pernah ke volume/log): user `default` OFF,
#    `efn-bff` dibatasi ke prefix key BFF, `efn-exporter` hanya perintah observasi.
#  - TLS saja (port 0, tls-port 6379), TLS1.2+, tanpa client cert (autentikasi = ACL).
#  - maxmemory-policy noeviction: key keamanan (OTP, rate limit, token) tidak boleh dibuang diam-diam.
set -eu

ACL_FILE=/tmp/efn-users.acl
BFF_PW="$(cat /run/secrets/redis_password_bff)"
EXP_PW="$(cat /run/secrets/redis_password_exporter)"

for pw in "$BFF_PW" "$EXP_PW"; do
  case "$pw" in
    *[!A-Za-z0-9]*|'') echo "redis-entrypoint: password Redis harus alfanumerik (gunakan generator hex32)" >&2; exit 1 ;;
  esac
  if [ "${#pw}" -lt 32 ]; then echo "redis-entrypoint: password Redis < 32 karakter" >&2; exit 1; fi
done

umask 077
{
  echo "user default off resetkeys resetchannels -@all"
  # Prefix key BFF (G6 QA: diverifikasi terhadap kode, key kini ber-HMAC SEC-24):
  #   otp:<challenge_id> (hash challenge OTP), dtok:used:<jti> (discount token sekali pakai),
  #   rl:<key>:<bucket> (SEMUA rate limit: rl:otp:*, rl:altcha:ip:*, rl:dsar:export:*, rl:ms:daily:*, rl:paychk:<id>),
  #   altcha:used:<signature> (ALTCHA sekali pakai, docs/04 §8.1/§8.11).
  # ms:* dipertahankan sementara untuk kompatibilitas key lama (tidak lagi ditulis BFF; hapus fase 2).
  # Tambah prefix baru = ubah baris ini (koordinasi efn-bff-engineer).
  echo "user efn-bff on >${BFF_PW} resetkeys ~otp:* ~dtok:* ~rl:* ~altcha:used:* ~ms:* resetchannels +@all -@admin -@dangerous +info +client|setname +client|getname +client|id"
  echo "user efn-exporter on >${EXP_PW} resetkeys resetchannels -@all +@connection +info +ping +select +config|get +client|list +slowlog|get +slowlog|len +latency|latest +latency|histogram +memory|stats"
} > "$ACL_FILE"
# chmod SEBELUM chown: setelah chown root tidak lagi pemilik dan tanpa CAP_FOWNER chmod ditolak (gelombang 7)
chmod 0600 "$ACL_FILE"
chown redis:redis "$ACL_FILE"
unset BFF_PW EXP_PW pw

cd /data
exec /usr/bin/setpriv --reuid redis --regid redis --clear-groups redis-server \
  --port 0 \
  --tls-port 6379 \
  --tls-cert-file /run/secrets/tls_redis_crt \
  --tls-key-file /run/secrets/tls_redis_key \
  --tls-ca-cert-file /run/secrets/efn_ca_crt \
  --tls-auth-clients no \
  --tls-protocols "TLSv1.2 TLSv1.3" \
  --tls-prefer-server-ciphers yes \
  --aclfile "$ACL_FILE" \
  --appendonly yes \
  --maxmemory "${REDIS_MAXMEMORY:-512mb}" \
  --maxmemory-policy noeviction
