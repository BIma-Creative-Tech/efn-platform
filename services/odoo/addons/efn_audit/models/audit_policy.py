"""Kebijakan efn_audit yang di-hard-code (tidak bisa diubah lewat data/aturan) — Gelombang 11.

SEC-43 (tamper resistance), SEC-45 (minimisasi PII), SEC-46 (company lama). ADR-031, docs/08 §5.1.
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
    "ir.config_parameter": (("key", "value"), "[('key', '=like', 'efn.audit.%')]"),
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
}
RULE_MODEL = "efn.audit.rule"
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
_PII_TOKENS = frozenset({"vat", "nik", "nib", "ssnid", "sinid", "nitku"})
_ID_KINDS = ("vat", "nik", "nib", "npwp", "ktp", "passport", "identification", "ssnid", "sinid", "nitku", "acc_number")
PII_TEXT_TYPES = frozenset({"char", "text"})


def is_pii_field(fname, field=None):
    """True bila nama field menunjukkan PII yang wajib di-mask (denylist/masklist global)."""
    if field is not None and field.type not in PII_TEXT_TYPES:
        return False
    lname = fname.lower()
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
    if "phone" in lname or "mobile" in lname:
        return mask_phone(normalize_member_ref(text) or text)
    if "email" in lname:
        return mask_email(text)
    if any(part in lname for part in _ID_KINDS):
        return mask_identifier(text)
    return f"****({len(text)})"
