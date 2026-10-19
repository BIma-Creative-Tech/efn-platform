"""Konstanta & helper murni efn_role (G21, E-47/E-47c; docs/08 §12.3-§12.5, docs/04 §13.3, L-64, L-65, L-67).

Penanda operasi internal disimpan di `cr.cache` (per cursor/transaksi), BUKAN di context: context dapat diisi
klien lewat RPC (`/web/dataset/call_kw`, `/json/2`), `cr.cache` tidak (pola sama dengan efn_audit / efn_pos_otp_gate).
Spesifikasi menyebut `with_context(efn_role_apply=True)`; diganti penanda `cr.cache` agar tidak bisa dipalsukan klien.
"""

import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta

from odoo import SUPERUSER_ID, fields

JAKARTA_TZ = "Asia/Jakarta"

# Penanda cr.cache.
APPLY_KEY = "efn_role_apply"  # penerapan peran -> boleh menulis grup terkelola ke user ber-flag
INTERNAL_KEY = "efn_role_internal"  # tulis field status/snapshot/flag oleh kode server

# Grup admin: peran yang (efektif) memuat salah satunya hanya boleh dibuat/diubah/ditetapkan oleh base.group_system.
ADMIN_GROUP_XMLIDS = ("base.group_system", "base.group_erp_manager")
# Grup sensitif (SEC-154, L-67): peran yang (efektif) memuat salah satunya TIDAK PERNAH disinkron dari jabatan
# (`efn.role.sync_from_job` ditolak) -> penetapan manual oleh Access Rights / Superadmin. Opsional per xmlid.
# Parameter `efn_role.sensitive_group_xmlids` (xmlid dipisah koma) hanya MENAMBAH daftar ini, tidak mengurangi.
SENSITIVE_GROUP_XMLIDS = (
    *ADMIN_GROUP_XMLIDS,
    "account.group_account_manager",
    "sales_team.group_sale_manager",
    "point_of_sale.group_pos_manager",
    "efn_audit.group_manager",
    "efn_role.group_refund_approver",
    "efn_loyalty_accounting.group_loyalty_manager",
)
SENSITIVE_PARAM = "efn_role.sensitive_group_xmlids"
XMLID_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
# Akun non-jabatan (docs/08 §12.2b): tidak boleh diberi peran. Opsional (modul pemilik mungkin belum terpasang).
TECHNICAL_GROUP_XMLIDS = ("efn_online_order.group_online_channel", "efn_pos_kds.group_kds_device")
# Grup penanda modul ini (tanpa ACL, tanpa implied_ids) -> selalu termasuk himpunan terkelola.
MARKER_GROUP_XMLIDS = ("efn_role.group_role_cashier", "efn_role.group_refund_approver")
# Grup yang diberikan otomatis oleh CE (bukan "pemberian langsung" di laporan review).
AUTO_GROUP_XMLIDS = ("base.group_user", "base.group_multi_company")

# Pengecualian SoD (L-65).
EXCEPTION_MIN_REASON = 20
EXCEPTION_MAX_DAYS = 90
EXCEPTION_DEFAULT_DAYS = 30

# Review akses (§12.5b).
STALE_LOGIN_DAYS = 90
REVIEW_CATCHUP_DAYS = 3  # cron harian membuat review periode lalu bila tanggal (WIB) <= 3


@contextmanager
def cache_flag(cr, key):
    """Pasang penanda `key` di `cr.cache` selama blok (bersarang aman)."""
    previous = cr.cache.get(key)
    cr.cache[key] = True
    try:
        yield
    finally:
        if previous:
            cr.cache[key] = previous
        else:
            cr.cache.pop(key, None)


def has_flag(cr, key):
    return bool(cr.cache.get(key))


def efn_today(env):
    """Tanggal bisnis hari ini menurut Asia/Jakarta (bukan zona user/server)."""
    return fields.Date.context_today(env["res.users"].with_context(tz=JAKARTA_TZ))


def previous_period(day):
    """`YYYY-MM` bulan sebelum `day`."""
    prev = day.replace(day=1) - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def xmlid_group_ids(env, xmlids):
    """Id grup untuk xmlid yang ada (yang belum terpasang dilewati)."""
    ids = set()
    for xmlid in xmlids:
        record = env.ref(xmlid, raise_if_not_found=False)
        if record is not None and record._name == "res.groups":
            ids.add(record.id)
    return ids


# ------------------------------------------------------------------ graf implied_ids
def implied_map(env):
    """{gid: {hid, ...}} dari tabel relasi `res_groups_implied_rel` (gid -> hid tersirat).

    Dibaca lewat SQL setelah flush agar tidak bergantung pada cache `groups` (`_get_group_definitions`) yang
    baru dibersihkan CE SETELAH `res.groups.write` selesai (constraint/override kita berjalan di dalamnya).
    """
    env["res.groups"].flush_model(["implied_ids"])
    env.cr.execute("SELECT gid, hid FROM res_groups_implied_rel")
    graph = defaultdict(set)
    for gid, hid in env.cr.fetchall():
        graph[gid].add(hid)
    return graph


def reverse_map(graph):
    rev = defaultdict(set)
    for gid, hids in graph.items():
        for hid in hids:
            rev[hid].add(gid)
    return rev


def closure(group_ids, graph, exclude=frozenset()):
    """Penutup refleksif-transitif `group_ids` pada `graph`, tanpa melewati grup `exclude`."""
    result = set()
    stack = [g for g in group_ids if g not in exclude]
    while stack:
        gid = stack.pop()
        if gid in result:
            continue
        result.add(gid)
        stack.extend(h for h in graph.get(gid, ()) if h not in result and h not in exclude)
    return result


def explicit_memberships(env, user_ids):
    """{uid: {gid}} keanggotaan eksplisit (tabel `res_groups_users_rel`) setelah flush kedua sisi relasi."""
    env["res.users"].flush_model(["group_ids"])
    env["res.groups"].flush_model(["user_ids"])
    result = defaultdict(set)
    ids = [uid for uid in user_ids if isinstance(uid, int)]
    if not ids:
        return result
    env.cr.execute("SELECT uid, gid FROM res_groups_users_rel WHERE uid = ANY(%s)", [ids])
    for uid, gid in env.cr.fetchall():
        result[uid].add(gid)
    return result


def users_of_groups(env, group_ids):
    """uid anggota eksplisit salah satu `group_ids`."""
    ids = [gid for gid in group_ids if isinstance(gid, int)]
    if not ids:
        return set()
    env["res.users"].flush_model(["group_ids"])
    env["res.groups"].flush_model(["user_ids"])
    env.cr.execute("SELECT DISTINCT uid FROM res_groups_users_rel WHERE gid = ANY(%s)", [ids])
    return {row[0] for row in env.cr.fetchall()}


def sensitive_group_ids(env):
    """Id grup sensitif (SEC-154): bawaan + tambahan parameter `efn_role.sensitive_group_xmlids`.

    Entri berformat salah / xmlid yang tidak ada dilewati; parameter tidak bisa mengurangi daftar bawaan."""
    raw = env["ir.config_parameter"].sudo().get_param(SENSITIVE_PARAM, "") or ""
    extra = tuple(x.strip() for x in raw.replace(";", ",").split(",") if XMLID_RE.match(x.strip()))
    return xmlid_group_ids(env, SENSITIVE_GROUP_XMLIDS + extra)


def effective_member_ids(env, xmlids):
    """uid yang EFEKTIF memegang salah satu grup `xmlids` (eksplisit atau lewat grup yang menyiratkannya),
    termasuk user arsip; dihitung dari tabel relasi setelah flush."""
    gids = xmlid_group_ids(env, xmlids)
    if not gids:
        return set()
    return users_of_groups(env, closure(gids, reverse_map(implied_map(env))))


# ----------------------------------------------------------------- penerima notifikasi
def _internal_active(users, company):
    return users.filtered(
        lambda u: u.active and not u.share and u.id != SUPERUSER_ID and (not company or company in u.company_ids)
    )


def auditor_users(env, company):
    """Auditor (efn_audit.group_auditor efektif) yang BUKAN admin hak akses, aktif, dengan `company`."""
    auditor = env.ref("efn_audit.group_auditor", raise_if_not_found=False)
    erp = env.ref("base.group_erp_manager", raise_if_not_found=False)
    if auditor is None:
        return env["res.users"]
    users = _internal_active(auditor.sudo().all_user_ids, company)
    if erp is not None:
        users = users.filtered(lambda u: erp not in u.all_group_ids)
    return users


def superadmin_users(env, company):
    system = env.ref("base.group_system")
    return _internal_active(system.sudo().all_user_ids, company)
