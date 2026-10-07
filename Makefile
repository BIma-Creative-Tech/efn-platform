# Perintah harian developer. Butuh Docker Desktop / docker engine + compose plugin, openssl, jq.
COMPOSE_DIR := infra/compose
DEV := docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.dev.yml --env-file $(COMPOSE_DIR)/.env
PROD := docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.prod.yml --env-file $(COMPOSE_DIR)/.env
ALL_PROFILES := --profile core --profile data --profile ai --profile observability --profile ops
PROFILES ?= --profile core

.PHONY: help secrets pki pki-rotate pki-root pki-intermediate env up down logs ps build validate lint lint-enc test test-py clean backup restore-test

help:
	@echo "make secrets       - buat secrets dev + PKI internal di ./secrets (daftar: scripts/secrets.list)"
	@echo "make pki           - (ulang) periksa/terbitkan sertifikat leaf dev (root+intermediate DEV otomatis)"
	@echo "make pki-rotate    - terbitkan ulang SEMUA sertifikat leaf dev, lalu recreate stack"
	@echo "make pki-root      - [SUPERADMIN, mesin offline] buat ROOT CA (kunci terenkripsi passphrase)"
	@echo "make pki-intermediate ENV=staging|production - [SUPERADMIN] terbitkan intermediate dari root offline"
	@echo "make env           - salin .env.example -> .env"
	@echo "make up            - jalankan stack dev (PROFILES='--profile core --profile data' untuk tambah DWH)"
	@echo "make down          - hentikan stack"
	@echo "make logs          - tail log semua service"
	@echo "make validate      - docker compose config (dev & prod, semua profile)"
	@echo "make lint          - lint lokal (ruff, hadolint, shellcheck via docker)"
	@echo "make lint-enc      - cek tidak ada koneksi plaintext (scripts/check-encryption.sh)"
	@echo "make test          - unit test api-gateway"
	@echo "make test-py       - pytest ai-ocr-worker, etl, text2sql"
	@echo "make backup        - base backup WAL-G sekarang (stack harus jalan)"
	@echo "make restore-test  - pulihkan backup terakhir ke container sementara + verifikasi"

secrets:
	bash scripts/gen-secrets.sh

pki:
	bash scripts/gen-pki.sh leaf

pki-rotate:
	FORCE=1 bash scripts/gen-pki.sh leaf
	$(DEV) $(PROFILES) up -d --force-recreate

# PKI produksi (docs/runbooks/key-custody.md, internal-pki.md). TIDAK PERNAH dijalankan di server/CI.
pki-root:
	bash scripts/gen-pki.sh root

pki-intermediate:
	@test -n "$(ENV)" || { echo "pemakaian: make pki-intermediate ENV=staging|production"; exit 1; }
	bash scripts/gen-pki.sh intermediate $(ENV)

env:
	@test -f $(COMPOSE_DIR)/.env || cp $(COMPOSE_DIR)/.env.example $(COMPOSE_DIR)/.env

up: env secrets
	$(DEV) $(PROFILES) up -d --build

down:
	$(DEV) $(ALL_PROFILES) down

logs:
	$(DEV) $(ALL_PROFILES) logs -f --tail=100

ps:
	$(DEV) $(ALL_PROFILES) ps

build:
	$(DEV) --profile core --profile data --profile ai build

validate: env secrets
	$(DEV) $(ALL_PROFILES) config -q
	$(PROD) $(ALL_PROFILES) config -q

# Image linter dipin digest (SEC-47; versi = hadolint-action v3.1.0 di CI). Dependabot tidak membaca Makefile:
# perbarui manual bersama ci.yml. Shellcheck: daftar file sama dengan job CI `infra`.
HADOLINT_IMG := hadolint/hadolint:v2.12.0@sha256:30a8fd2e785ab6176eed53f74769e04f125afb2f74a6c52aef7d463583b6d45e
SHELLCHECK_IMG := koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d

lint:
	ruff check services/ai-ocr-worker services/etl services/odoo/addons
	docker run --rm -i -v "$(PWD):/src" -w /src $(HADOLINT_IMG) hadolint --config .hadolint.yaml --failure-threshold warning \
	  services/*/Dockerfile infra/backup/postgres/Dockerfile infra/minio/Dockerfile
	docker run --rm -v "$(PWD):/src" -w /src $(SHELLCHECK_IMG) scripts/*.sh scripts/host/efn-deploy scripts/host/*.sh \
	  scripts/smoke/*.sh infra/backup/postgres/*.sh infra/postgres/init/*.sh infra/wireguard/*.sh
	docker run --rm -v "$(PWD):/src" -w /src $(SHELLCHECK_IMG) -s sh infra/minio/*.sh infra/redis/*.sh \
	  infra/rabbitmq/*.sh infra/clickhouse/*.sh infra/s3-gateway/*.sh infra/acme/*.sh scripts/acme-test/*.sh

lint-enc: env secrets
	bash scripts/check-encryption.sh
	bash scripts/update-cloudflare-ips.sh --check

test:
	cd services/api-gateway && npm ci && npm test

test-py:
	@for s in ai-ocr-worker etl text2sql; do \
	  if [ -d services/$$s/tests ]; then (cd services/$$s && python -m pytest -q tests) || exit 1; fi; \
	done

# Backup & restore test memakai container dev (secrets di ./secrets, metrik ke /tmp/efn-metrics)
backup:
	TEXTFILE_DIR=/tmp/efn-metrics bash scripts/backup.sh

restore-test:
	SECRETS_DIR=$(PWD)/secrets TEXTFILE_DIR=/tmp/efn-metrics bash scripts/restore-test.sh

clean: down
	$(DEV) $(ALL_PROFILES) down -v
