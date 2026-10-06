#!/bin/sh
# Entrypoint s3-gateway (HAProxy): HAProxy membutuhkan sertifikat + kunci dalam SATU file PEM, sedangkan Docker
# secret menyediakan dua file terpisah -> digabung ke tmpfs (/tmp, 0600, tidak pernah ke disk/volume), lalu exec.
# ADR-030 Keputusan 2; konfigurasi: infra/s3-gateway/haproxy.cfg.
set -eu
umask 077
mkdir -p /tmp/haproxy
{
  cat /run/secrets/tls_s3_gateway_crt
  echo
  cat /run/secrets/tls_s3_gateway_key
} > /tmp/haproxy/s3-gateway.pem
exec haproxy -W -db -f /usr/local/etc/haproxy/haproxy.cfg
