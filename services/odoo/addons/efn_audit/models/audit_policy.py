"""Kebijakan efn_audit yang di-hard-code (tidak bisa diubah lewat data/aturan) — Gelombang 11.

SEC-43 (tamper resistance), SEC-45 (minimisasi PII), SEC-46 (company lama). ADR-031, docs/08 §5.1.
Gelombang 13: SEC-49 (perubahan hak akses = model kontrol), SEC-56 (alamat, tanggal lahir, login).
Fungsi di sini murni (tanpa env) agar mudah diuji dan dipakai ulang oleh `base.py`,
`efn_audit_log.py`, `efn_audit_rule.py`, dan `ir_config_parameter.py`.

Batas yang diakui (README "Keamanan"): admin sistem (`base.group_system`) tetap dapat menjalankan
kode Python lewat server action/cron dan meng-uninstall modul. Lapisan di sini membuat setiap
langkah itu **meninggalkan jejak** (log kontrol tidak ikut GC + baris sink ke Loki setelah commit),
bukan membuatnya mustahil. Salinan tamper-evident sesungguhnya = sink eksternal (Loki/ClickHouse).
"""

import re

from odoo.addons.efn_core.models.efn_bff_client import mask_phone, normalize_member_ref

# ------------------------------------------------------------------ retensi (SEC-43)
RETENTION_PARAM = "efn.audit.retention_days"
# Batas bawah retensi (hari). Nilai > 0 di bawah ini ditolak (parameter & aturan) dan, bila
# tetap lolos (mis. SQL langsung), diperlakukan sebagai minimum oleh GC. 0 = simpan selamanya.
# Menaikkan nilai ini = keputusan auditor/DPO (Q-6, Q-49); menurunkannya butuh rilis kode (PR).
MIN_RETENTION_DAYS = 365
DEFAULT_RETENTION_DAYS = 730


def parse_retention(raw):
    """Nilai parameter retensi -> int, atau None bila bukan bilangan bulat >= 0."""
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return days if days >= 0 else None


def retention_error(days):
    """Pesan galat (str) bila `days` tidak sah, atau None. 0 = simpan selamanya (sah)."""
    if days is None or days < 0:
        return "Retensi log audit harus bilangan bulat >= 0 (0 = simpan selamanya)."
    if 0 < days < MIN_RETENTION_DAYS:
        return (
            f"Retensi log audit minimal {MIN_RETENTION_DAYS} hari (SEC-43; 0 = simpan selamanya). Nilai {days} ditolak."
        )
    return None


def effective_retention(days):
    """Retensi efektif untuk GC: 0 tetap 0 (selamanya); 1..MIN-1 dinaikkan ke MIN."""
    if not days or days <= 0:
        return 0
    return max(days, MIN_RETENTION_DAYS)


# ---------------------------------------------------- model kontrol (SEC-43 butir 2-3)
# Model yang SELALU diaudit walau tidak ada aturan / aturannya diarsipkan (spesifikasi bawaan),
# dan lognya TIDAK PERNAH dihapus GC retensi. Nilai: (field dilacak | None = semua field
# memenuhi syarat, filter domain | None = semua record). Aturan admin untuk model yang sama
# hanya MENAMBAH (gabungan field, OR filter), tidak bisa mengurangi.
CONTROL_SPECS = {
    # Parameter retensi & kebijakan audit itu sendiri.
    # SEC-75 (Gelombang 15): sakelar pembuka QR-order ponsel (`pos_self_order` mode mobile/QR menu)
    # juga parameter kontrol: selalu diaudit, log tidak ikut GC.
    "ir.config_parameter": (
        ("key", "value"),
        "['|', ('key', '=like', 'efn.audit.%'), ('key', 'in', ['efn.pos.self_order_mobile_allowed'])]",
    ),
    # Kode Python yang dijalankan server (jalur yang bisa memanggil metode privat / SQL).
    "ir.actions.server": (None, None),
    # Jadwal cron. nextcall/lastcall/failure_* sengaja tidak dilacak (berubah setiap jalan).
    "ir.cron": (("active", "interval_number", "interval_type", "user_id", "priority", "ir_actions_server_id"), None),
    # Install/uninstall modul (uninstall efn_audit = drop tabel log). Penulisan saat registry
    # belum siap (proses install/upgrade itu sendiri) tidak tercatat — lihat SEC-46.
    "ir.module.module": (("state",), None),
    # Otomasi (modul CE base_automation, bila terpasang). last_run tidak dilacak.
    "base.automation": (
        (
            "name",
            "active",
            "trigger",
            "model_id",
            "filter_domain",
            "filter_pre_domain",
            "trg_date_id",
            "trg_field_ref",
            "trigger_field_ids",
            "on_change_field_ids",
            "action_server_ids",
        ),
        None,
    ),
    # ---- SEC-49 (Gelombang 13): perubahan hak akses. Odoo CE 19 tidak mencatatnya di chatter.
    # Keanggotaan grup (relasi res_groups_users_rel) bisa diubah dari dua sisi: `res.users.group_ids`
    # dan `res.groups.user_ids` -> keduanya dilacak. `share` = compute stored (ikut bila ada di vals).
    "res.users": (("group_ids", "active", "login", "company_ids", "company_id", "share"), None),
    # Grup: anggota, implikasi dua arah, rule/menu/ACL terkait, nama. None = semua field memenuhi syarat
    # (o2m `model_access` tidak ikut — perubahannya tercatat di ir.model.access sendiri).
    "res.groups": (None, None),
    # Record rule multi-company & ACL: semua field (domain_force, groups, perm_*, active, model_id).
    "ir.rule": (None, None),
    "ir.model.access": (None, None),
    # API key: create (Odoo menulis lewat SQL -> hook `_generate`, models/res_users_apikeys.py) dan
    # unlink. Kolom `key`/`index` bukan field ORM dan juga ada di denylist (efn_audit_rule.DENY_FIELDS).
    "res.users.apikeys": (("name", "user_id", "scope", "expiration_date"), None),
}
RULE_MODEL = "efn.audit.rule"
# SEC-49: model hak akses -> baris sink membawa `audit_op` khusus + daftar grup (xmlid) yang
# diberikan/dicabut dan uid yang terdampak (tanpa nama/login).
ACCESS_MODELS = frozenset({"res.users", "res.groups", "ir.rule", "ir.model.access", "res.users.apikeys"})
# Pemberian grup yang (langsung atau lewat implied) mencakup salah satu grup ini -> `critical: true`
# di sink (alert Loki critical). Eskalasi admin / akses log data pribadi lintas PT.
CRITICAL_GROUP_XMLIDS = frozenset(
    {
        "base.group_system",
        "base.group_erp_manager",
        "efn_audit.group_auditor_personal",
        "efn_audit.group_manager",
    }
)
# Log model-model ini + log operasi `purge` dikecualikan dari GC retensi.
PROTECTED_LOG_MODELS = frozenset(CONTROL_SPECS) | {RULE_MODEL}

# ------------------------------------------------------------ PII (SEC-45, UU PDP)
# Model yang record-nya = orang. Log model ini (dan log apa pun yang memuat baris ter-mask)
# ditandai `is_personal`; tanpa company hanya terbaca oleh grup Auditor Data Pribadi Lintas PT.
# `res_name` model ini disimpan sebagai `model,id` (tanpa nama).
PERSONAL_MODELS = frozenset({"res.partner", "res.users", "res.partner.bank", "hr.employee"})
# Field nama orang -> inisial (`B*** S***`). res.partner perusahaan (`is_company`) tidak di-mask.
PERSON_NAME_FIELDS = {
    "res.partner": frozenset({"name", "complete_name"}),
    "res.users": frozenset({"name"}),
    "hr.employee": frozenset({"name"}),
}
# Masking wajib berbasis pola nama field (char/text), berlaku walau tidak dipilih di aturan.
_PII_SUBSTRINGS = ("phone", "mobile", "email", "npwp", "ktp", "passport", "identification", "acc_number")
_PII_TOKENS = frozenset(
    {
        "vat",
        "nik",
        "nib",
        "ssnid",
        "sinid",
        "nitku",
        # SEC-56 (Gelombang 13): alamat rinci & login (umumnya email/HP). `city`/`state`/`country`
        # sengaja tidak (granularitas rendah, dipakai analitik wilayah).
        "street",
        "street2",
        "street3",
        "zip",
        "address",
        "login",
    }
)
# SEC-56: tanggal lahir (tipe date/datetime/char) — substring agar `birthday`, `date_of_birth`,
# `birthdate` tertangkap.
_BIRTH_SUBSTRING = "birth"
BIRTH_TYPES = frozenset({"date", "datetime", "char"})
_ID_KINDS = ("vat", "nik", "nib", "npwp", "ktp", "passport", "identification", "ssnid", "sinid", "nitku", "acc_number")
PII_TEXT_TYPES = frozenset({"char", "text"})


def is_pii_field(fname, field=None):
    """True bila nama field menunjukkan PII yang wajib di-mask (denylist/masklist global)."""
    lname = fname.lower()
    if _BIRTH_SUBSTRING in lname and (field is None or field.type in BIRTH_TYPES):
        return True
    if field is not None and field.type not in PII_TEXT_TYPES:
        return False
    if any(part in lname for part in _PII_SUBSTRINGS):
        return True
    return any(token in _PII_TOKENS for token in re.split(r"[^a-z0-9]+", lname))


def mask_email(value):
    """`budi@example.com` -> `b***@example.com` (UU PDP)."""
    if not value or "@" not in value:
        return "****"
    local, _sep, domain = value.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_identifier(value):
    """NPWP/NIK/rekening: hanya 3 digit terakhir bila >= 10 digit (cukup untuk melihat ada
    perubahan), selain itu `****(panjang)`. Nilai lengkap tidak disimpan."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) >= 10:
        return f"****{digits[-3:]}"
    return f"****({len(value)})"


def mask_person_name(value):
    """`Budi Santoso` -> `B*** S***`."""
    words = [w for w in (value or "").split() if w]
    if not words:
        return "****"
    return " ".join(f"{w[:1]}***" for w in words[:6])


def mask_value(fname, text, kind=None):
    """Samarkan nilai PII SEBELUM masuk DB. `kind="name"` untuk nama orang."""
    if not text:
        return text
    if kind == "name":
        return mask_person_name(text)
    lname = fname.lower()
    if _BIRTH_SUBSTRING in lname:
        return "****-**-**"
    if "login" in lname:
        # Login umumnya email atau HP; selain itu hanya panjang.
        if "@" in text:
            return mask_email(text)
        if re.fullmatch(r"\+?[\d\s-]{8,}", text):
            return mask_phone(normalize_member_ref(text) or text)
        return f"****({len(text)})"
    if "phone" in lname or "mobile" in lname:
        return mask_phone(normalize_member_ref(text) or text)
    if "email" in lname:
        return mask_email(text)
    if any(part in lname for part in _ID_KINDS):
        return mask_identifier(text)
    return f"****({len(text)})"


# ------------------------------------------------- teks bebas (SEC-78, Gelombang 15)
# Catatan purge manual = teks bebas pemanggil/admin -> disimpan di DB + sink Loki (400 hari, stream
# sink tidak diredaksi Promtail agar MAC utuh). Pola PII di-mask DI SUMBER, sebelum MAC.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# HP Indonesia dengan pemisah opsional: 0812-3456-7890, +62 812 3456 7890, (+62) 812..., 6281234567890.
_PHONE_RE = re.compile(r"(?<![\d+])(?:\(\+?62\)|\+?62|0)[\s.-]?8[1-9](?:[\s.-]?\d){6,11}(?!\d)")
# NIK (16 digit) / NPWP 15-16 digit polos, dan NPWP berformat 99.999.999.9-999.999.
_ID_RE = re.compile(r"(?<!\d)(?:\d{15,16}|\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3})(?!\d)")


def mask_free_text(text):
    """Mask HP/email/NIK/NPWP di dalam teks bebas (catatan purge). Teks lain dibiarkan."""
    if not text or not isinstance(text, str):
        return text
    text = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
    text = _PHONE_RE.sub(lambda m: mask_phone(normalize_member_ref(m.group(0)) or m.group(0)), text)
    return _ID_RE.sub(lambda m: mask_identifier(m.group(0)), text)
