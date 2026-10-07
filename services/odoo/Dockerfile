# Odoo CE 19 + addons EFN saja (ADR-031: tanpa modul OCA/Cybrosys/vendor). Image resmi non-root (user odoo).
# TLS (docs/04 §7.1): Odoo HTTP listen di 127.0.0.1 (http_interface) dan dibungkus sidecar
# ghostunnel yang berbagi network namespace; DB lewat db_sslmode=verify-full.
# SEC-47: base image dipin tag@digest (build reprodusibel); Dependabot `docker` memperbarui digest per tag.
#
# Stage (Gelombang 12):
#   runtime    -> isi image produksi.
#   test       -> runtime + Google Chrome headless + python3-websocket, KHUSUS test UI POS
#                 (HttpCase/tour, tag `efn_tour`). Hanya dibangun dengan `--target test` (CI job
#                 odoo-tour); tidak pernah di-push/di-deploy.
#   production -> stage TERAKHIR = target default `docker build` (compose, release). Chrome tidak ikut.
FROM odoo:19.0@sha256:dd9013e669caaa23d26765dc55814655eaeecca7cfc2c265dbabae913bce22fd AS runtime

USER root

# Dependensi Python tambahan untuk addons (pin versi di requirements.txt)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    # SEC-30: render_odoo_conf.py gagal bila passlib tidak ada (tanpa fallback plaintext);
    # pastikan saat build bahwa image memang menyertakannya.
    && python3 -c "import passlib.context"

# Hanya addons efn_* dari repo; tidak ada fetch kode pihak ketiga saat build (ADR-031,
# gerbang CI scripts/check-odoo-deps.sh).
COPY --chown=odoo:odoo addons /mnt/extra-addons
# Template & skrip render milik root (read-only bagi user odoo); hanya odoo.conf hasil
# render yang ditulis user odoo di /etc/odoo (mode 0600).
COPY --chown=root:root --chmod=0644 odoo.conf.tpl /etc/odoo/odoo.conf.tpl
COPY --chown=root:root --chmod=0755 render_odoo_conf.py /usr/local/lib/efn/render_odoo_conf.py
COPY --chown=root:root --chmod=0755 entrypoint-efn.sh /entrypoint-efn.sh
RUN chown -R odoo:odoo /mnt/extra-addons \
    && chown odoo:odoo /etc/odoo \
    && rm -f /etc/odoo/odoo.conf

USER odoo

# 127.0.0.1 eksplisit: Odoo hanya terikat ke loopback (localhost bisa resolve ke ::1).
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8069/web/health || exit 1

ENTRYPOINT ["/entrypoint-efn.sh"]
CMD ["odoo"]

# ---------------------------------------------------------------------------------------------
# Stage test: browser headless untuk tour POS (Gelombang 12). Ubuntu 24.04 (base odoo:19.0)
# hanya punya `chromium` versi snap (tidak jalan di container), jadi dipakai Google Chrome dari
# repo apt resmi Google. Kunci repo diverifikasi fingerprint-nya sebelum dipercaya (signed-by),
# sehingga paket diverifikasi GPG oleh apt. Versi Chrome = stable saat build (repo Google tidak
# menyimpan versi lama) -> image test tidak reprodusibel; dapat diterima karena tidak pernah
# di-deploy (catatan di README efn_pos_otp_gate).
FROM runtime AS test

USER root
ARG GOOGLE_LINUX_SIGNING_FPR=EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796
RUN set -eux; \
    curl -fsSL --proto '=https' --tlsv1.2 https://dl.google.com/linux/linux_signing_key.pub -o /tmp/google.pub; \
    gpg --batch --show-keys --with-colons /tmp/google.pub > /tmp/google.keys; \
    grep -q "^fpr:::::::::${GOOGLE_LINUX_SIGNING_FPR}:" /tmp/google.keys; \
    gpg --batch --dearmor -o /usr/share/keyrings/google-linux.gpg /tmp/google.pub; \
    rm /tmp/google.pub /tmp/google.keys; \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends google-chrome-stable python3-websocket fonts-liberation; \
    rm -rf /var/lib/apt/lists/*; \
    google-chrome --version; \
    python3 -c "import websocket"
USER odoo

# ---------------------------------------------------------------------------------------------
# Target default (stage terakhir): image produksi tanpa browser.
FROM runtime AS production
