# Odoo CE 19 + addons EFN saja (ADR-031: tanpa modul OCA/Cybrosys/vendor). Image resmi non-root (user odoo).
# TLS (docs/04 §7.1): Odoo HTTP listen di 127.0.0.1 (http_interface) dan dibungkus sidecar
# ghostunnel yang berbagi network namespace; DB lewat db_sslmode=verify-full.
FROM odoo:19.0

USER root

# Dependensi Python tambahan untuk addons (pin versi di requirements.txt)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

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
