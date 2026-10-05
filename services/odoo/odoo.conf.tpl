; Template odoo.conf (non-rahasia). Dirender oleh render_odoo_conf.py saat container start.
; JANGAN menaruh admin_passwd / db_* / http_interface / workers di sini: nilai itu di-set
; entrypoint dari env + Docker secrets (SEC-15) dan render akan gagal bila kunci rahasia ada.
; Komentar file ini tidak ikut ke odoo.conf hasil render.
[options]
addons_path = /mnt/extra-addons,/mnt/oca-addons,/usr/lib/python3/dist-packages/odoo/addons
data_dir = /var/lib/odoo

; Keamanan (R-S03, SEC-01). proxy_mode juga dipaksa True oleh render (di belakang
; sidecar TLS ghostunnel + Traefik).
list_db = False
proxy_mode = True
dbfilter = ^efn_.*$

; Performa (R-T11): worker HTTP terpisah dari cron. workers/max_cron_threads dari env.
limit_memory_hard = 2684354560
limit_memory_soft = 2147483648
limit_request = 8192
limit_time_cpu = 120
limit_time_real = 240
limit_time_real_cron = 600

; Logging ke stdout agar ditangkap Promtail
logfile =
log_level = info
log_handler = :INFO,werkzeug:WARNING

; Websocket/longpolling (POS). Terikat ke http_interface yang sama (127.0.0.1 di pola sidecar).
gevent_port = 8072

; Email dikirim via BFF/notification service, bukan SMTP langsung dari Odoo.
; (email_from = False dihapus: Odoo 19 men-skip nilai 'False' untuk opsi non-boolean.)
