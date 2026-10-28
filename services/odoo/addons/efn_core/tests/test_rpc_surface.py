"""SEC-58 (r6, High): permukaan RPC addon ``efn_*``.

Odoo mengekspos SETIAP metode model tanpa awalan ``_`` (dan tanpa ``@api.private``) ke
``/web/dataset/call_kw`` dan ``/json/2`` (``odoo/service/model.py::get_public_method``), hanya
dengan ``browse(ids)`` -- tanpa cek grup, record rule, atau company. Karena itu:

1. **Gerbang statis (AST)**: setiap metode publik di kelas model ``efn_*`` wajib tercantum di
   :data:`RPC_ALLOWLIST` beserta alasan/cek aksesnya (ditinjau seperti ``route-pii-audit`` BFF).
   AbstractModel ``efn.*`` (mis. ``efn.bff.client``, proxy service key BFF) TIDAK boleh punya
   metode publik sama sekali. Menambah metode publik baru = menambah entri allowlist (review).
2. **Gerbang registry**: metode publik BARU (bukan override metode CE) yang dapat dipanggil RPC
   pada model terpasang harus ada di allowlist; ``efn.bff.client`` tidak punya satu pun.
3. **HttpCase**: user internal biasa memanggil klien BFF lewat ``call_kw`` -> ditolak dan tidak ada
   request HTTP ke BFF. ``/json/2`` (modul ``rpc``, ``controllers/json2.py``) memakai
   ``get_public_method`` yang sama, jadi dicakup gerbang registry (HttpCase ``/json/2`` dengan
   bearer key buatan test menggantung pada kunci baris di harness ini -> tidak dipakai). Uji kasir/POS ada di
   ``efn_pos_loyalty_ledger/tests/test_rpc_access.py``.
"""

import ast
import inspect
import json
import pathlib
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import BaseCase, HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

ADDONS_DIR = pathlib.Path(__file__).resolve().parents[2]

# Override metode ORM/CE yang memang publik di Odoo standar (hak akses = ACL + record rule CE).
ORM_OVERRIDES = frozenset({"create", "write", "unlink", "copy", "copy_data", "read", "search", "name_create"})

# (model, metode) -> cek akses eksplisit di server. WAJIB ditinjau security sebelum ditambah.
RPC_ALLOWLIST = {
    # efn_pos_otp_gate / efn_pos_loyalty_ledger: `_efn_check_otp_caller` = grup
    # point_of_sale.group_pos_user + check_access(read) config + company user + sesi terbuka.
    ("pos.config", "efn_otp_request"): "_efn_check_otp_caller",
    ("pos.config", "efn_otp_verify"): "_efn_check_otp_caller",
    ("pos.config", "efn_loyalty_balance"): "_efn_check_otp_caller",
    (
        "pos.config",
        "efn_loyalty_redeem_reserve",
    ): "_efn_check_otp_caller + redeem_enabled + OTP pos_redeem",
    ("pos.config", "efn_loyalty_redeem_release"): "_efn_check_otp_caller",
    (
        "pos.config",
        "efn_loyalty_refund_preview",
    ): "_efn_check_otp_caller + check_access order asal + company",
    # efn_pos_payment_gateway: `_efn_gateway_check_caller` = grup POS user + metode efn_gateway +
    # check_access(read) config + metode milik config + sesi terbuka.
    ("pos.payment.method", "efn_gateway_create"): "_efn_gateway_check_caller",
    (
        "pos.payment.method",
        "efn_gateway_status",
    ): "_efn_intent_for_rpc -> _efn_gateway_check_caller",
    (
        "pos.payment.method",
        "efn_gateway_cancel",
    ): "_efn_intent_for_rpc -> _efn_gateway_check_caller",
    # efn_pos_loyalty_ledger (SEC-60): tombol supervisor = grup POS manager + record rule + company.
    ("efn.pos.loyalty.outbox", "action_efn_hold_approve"): "_efn_check_hold_decider",
    ("efn.pos.loyalty.outbox", "action_efn_hold_cancel"): "_efn_check_hold_decider",
    # L-26 (SEC-128): supervisor menyetujui nilai restore yang ditolak BFF -> kirim ulang `restore_approved`.
    ("efn.pos.loyalty.outbox", "action_efn_restore_approve"): "_efn_check_hold_decider",
    # efn_pos_refund_origin (G18, E-31): `_efn_refund_check_caller` = grup POS user + check_access(read) config +
    # company user + sesi terbuka; order asal dibaca dengan hak user (record rule) + cek company; hanya id metode &
    # nominal alokasi (tanpa data pelanggan).
    (
        "pos.config",
        "efn_refund_origin_allocation",
    ): "_efn_refund_check_caller + check_access order asal + company",
    # efn_pos_refund_origin (G20, E-41, docs/04 §12.4, L-51): retur tanpa struk dengan persetujuan Store Manager.
    # approvers: _efn_refund_check_caller + mode manager_approval; hanya {id, name} user internal aktif grup POS
    # manager dengan company config ∈ company user, PIN diatur, bukan pemanggil (tanpa hash/PII pelanggan).
    (
        "pos.config",
        "efn_return_approval_approvers",
    ): "_efn_refund_check_caller + mode manager_approval",
    # request: _efn_refund_check_caller + mode + validasi input manual + approver sah (grup POS manager, company
    # config, aktif/internal, PIN diatur, != kasir) + touch_row approver + kunci 15 menit setelah 5 PIN salah +
    # verifikasi pbkdf2 server; tulis efn.pos.return.approval via sudo setelah cek (TTL 5 menit, sekali pakai).
    (
        "pos.config",
        "efn_return_approval_request",
    ): "_efn_refund_check_caller + approver sah + lockout + pbkdf2",
    # PIN Store Manager: @check_identity Odoo (kata sandi ulang) + target = diri sendiri / base.group_system;
    # hanya membuka wizard. Wizard: identity check sesi < 10 menit + target diri sendiri / base.group_system +
    # target grup POS manager; hash pbkdf2 ditulis sudo.
    (
        "res.users",
        "efn_action_return_pin_wizard",
    ): "@check_identity + diri sendiri / base.group_system",
    (
        "efn.pos.return.pin.wizard",
        "action_apply",
    ): "identity check sesi + diri sendiri / base.group_system",
    # efn_pos_kds (Gelombang 14, docs/08 §7.3): `_efn_kds_check_device` = grup perangkat KDS
    # (user perangkat stasiun itu) ATAU POS manager + check_access(read) (record rule company +
    # stasiun milik perangkat) + company user; tulis via sudo setelah cek.
    ("efn.kds.station", "efn_kds_load"): "_efn_kds_check_device",
    ("efn.kds.station", "efn_kds_heartbeat"): "_efn_kds_check_device",
    (
        "efn.kds.station",
        "action_open_screen",
    ): "_efn_kds_check_device (URL saja, tanpa token)",
    (
        "efn.kds.station",
        "action_rotate_token",
    ): "grup POS manager + check_access(write)",
    (
        "efn.kds.ticket",
        "efn_kds_bump",
    ): "_efn_kds_check_ticket_access -> _efn_kds_check_device + record rule",
    (
        "efn.kds.ticket.line",
        "efn_kds_line_done",
    ): "_efn_kds_check_ticket_access + check_access(read) baris",
    (
        "pos.config",
        "efn_kds_station_status",
    ): "grup POS user + check_access(read) config + company user",
    # Override metode publik CE point_of_sale (sudah RPC di standar; efn hanya menambah release).
    ("pos.order", "action_pos_order_cancel"): "override CE",
    ("pos.order", "remove_from_ui"): "override CE",
    # efn_pos_kds (Gelombang 15, SEC-77): override CE pos.session.load_data -> super() lalu catat
    # env.user (grup POS user) sebagai penerima notifikasi kasir KDS; tanpa data tambahan.
    ("pos.session", "load_data"): "override CE (+ catat penerima notifikasi KDS)",
    # efn_online_order (Gelombang 17, E-28, docs/04 §10.4.3 + §10.12): 10 metode BFF lewat odoo-rpc-gateway.
    # `_efn_online_check_caller` = grup group_online_channel + user ∈ channel.user_ids + company kanal ∈ company
    # user + kanal aktif; context klien dibuang kecuali lang; tulis via sudo setelah cek; input divalidasi manual.
    (
        "efn.online.channel",
        "efn_online_catalog",
    ): "_efn_online_check_caller (baca, field whitelist)",
    ("efn.online.channel", "efn_online_stock"): "_efn_online_check_caller (baca)",
    (
        "efn.online.channel",
        "efn_online_image",
    ): "_efn_online_check_caller (baca, hanya produk kanal)",
    (
        "efn.online.channel",
        "efn_online_quote",
    ): "_efn_online_check_caller; tanpa promo new() tanpa tulis DB; dengan "
    "promo SO nyata di savepoint yang di-rollback (nomor urut SO terpakai, FOR UPDATE NOWAIT program sale_loyalty, "
    "kartu/poin ikut rollback; SEC-141)",
    (
        "efn.online.order",
        "efn_online_place",
    ): "_efn_online_check_caller + idempotency_key/payload_sha256/quote_hash",
    (
        "efn.online.order",
        "efn_online_mark_paid",
    ): "_efn_online_check_caller + FOR UPDATE + idempotency_key",
    (
        "efn.online.order",
        "efn_online_cancel",
    ): "_efn_online_check_caller + FOR UPDATE (ref asing -> absent)",
    (
        "efn.online.order",
        "efn_online_refund",
    ): "_efn_online_check_caller + idempotency_key + kumulatif <= dibayar",
    (
        "efn.online.order",
        "efn_online_refund_settled",
    ): "_efn_online_check_caller + refund milik kanal + idempotensi",
    (
        "efn.online.order",
        "efn_online_status",
    ): "_efn_online_check_caller (baca, hanya ref kanal itu)",
    (
        "efn.online.order",
        "efn_online_set_invoice_party",
    ): "G21 E-39: _efn_online_check_caller + validasi profil + "
    "FOR UPDATE + PT PPN + efn_pii aktif (L-63) + idempotency_key + hanya placed; hasil {state} tanpa PII",
    # 3 metode UI staf (web client lewat WireGuard, bukan gateway).
    (
        "efn.online.order",
        "action_efn_ready",
    ): "grup stock.group_stock_user + check_access(read) + company user",
    (
        "efn.online.order",
        "action_efn_retry_events",
    ): "grup sales_team.group_sale_manager + check_access + company",
    # efn_online_pos_sale (re-review G17 SEC-105): override metode publik CE pos_sale; hanya MENOLAK SO online
    # lalu super() (hak akses tetap ACL/record rule CE pos_sale).
    (
        "sale.order",
        "load_sale_order_from_pos",
    ): "override CE pos_sale (tolak SO online)",
    (
        "efn.online.handover.wizard",
        "action_confirm",
    ): "grup stock user + check_access(read) order + company; kode = verifikasi pbkdf2, 5x salah -> review",
    # G21 E-44 (efn_loyalty_accounting): tombol kebijakan poin & retry; cek grup + check_access + company + state.
    (
        "efn.loyalty.policy",
        "action_efn_approve",
    ): "Accounting Manager + check_access(read) + company + draft + bukan pembuat/penyunting terakhir",
    ("efn.loyalty.policy", "action_efn_reject"): "sama dengan action_efn_approve",
    (
        "efn.loyalty.policy.outbox",
        "action_efn_retry",
    ): "Loyalty Manager/Accounting Manager + check_access + company + state failed",
    (
        "efn.loyalty.accrual",
        "action_efn_retry",
    ): "Accounting Manager + check_access + company + state belum final",
    (
        "efn.online.order",
        "efn_online_counter_refund_authorize",
    ): "L-60: _efn_online_check_caller + FOR UPDATE + "
    "idempotency_key + refund requested/cancel tanpa faktur + nominal persis",
    (
        "pos.config",
        "efn_online_counter_lookup",
    ): "E-45: grup POS user + check_access(read) config + company + sesi "
    "terbuka + rate limit sesi/kasir; digest KDF per company + outlet = gudang config; hanya {order_label, amount}",
    # efn_role (G21, E-47/E-47c, docs/08 §12.3-§12.5): tombol UI staf (web client lewat WireGuard, bukan gateway).
    (
        "efn.role",
        "action_efn_apply_job_roles",
    ): "grup base.group_erp_manager + check_access(write) + record rule "
    "company + peran admin hanya base.group_system; sinkron baris jabatan via sudo setelah cek",
    (
        "efn.access.review",
        "action_efn_snapshot",
    ): "grup efn_audit.group_auditor / base.group_erp_manager + "
    "check_access(read) + record rule company; hanya draft; snapshot via sudo",
    (
        "efn.access.review",
        "action_efn_start_review",
    ): "grup efn_audit.group_auditor + check_access(write) + record rule company; draft -> in_review",
    (
        "efn.access.review",
        "action_efn_done",
    ): "grup efn_audit.group_auditor + check_access(write) + record rule company; in_review -> done (read-only)",
    (
        "efn.access.review",
        "action_efn_export_csv",
    ): "grup efn_audit.group_auditor / base.group_system + "
    "check_access(read) + record rule company; CSV hanya dari baris snapshot review (tanpa login/kontak) -> attachment",
    # efn_pos_bff_order (G22, E-27, docs/04 §14.A.5 path 13-15 + §14.7 L-70/L-76): 3 metode BFF lewat odoo-rpc-gateway.
    # `_efn_qr_check_caller` = grup group_qr_bff + user internal aktif dengan TEPAT satu company = company config +
    # tanpa grup lain selain Internal User (fail closed) + context klien dibuang kecuali lang; sudo setelah cek.
    (
        "efn.pos.qr.order",
        "efn_qr_menu",
    ): "_efn_qr_check_caller (baca; produk menu POS + harga/pajak dihitung server)",
    (
        "efn.pos.qr.order",
        "efn_qr_submit",
    ): "_efn_qr_check_caller + idempotency_key/payload_sha256 + sesi meja open & "
    "belum kedaluwarsa + sesi POS opened + touch_row sesi + batas kiriman/item/nominal; order dibangun server (L-70)",
    (
        "efn.pos.qr.order",
        "efn_qr_status",
    ): "_efn_qr_check_caller (baca; hanya kiriman config itu, ref asing -> absent)",
    # efn_table_booking (G23, W-35, docs/04 §15.3 path 16-19): `_efn_booking_check_caller` = grup group_booking_bff +
    # user internal aktif TEPAT satu company = company outlet + tanpa grup lain (fail closed) + context dibuang kecuali
    # lang; sudo setelah cek. Tombol staf = grup POS manager + check_access(write) + company.
    (
        "efn.table.booking",
        "efn_booking_availability",
    ): "_efn_booking_check_caller (baca; jam buka + meja + bentrok)",
    (
        "efn.table.booking",
        "efn_booking_create",
    ): "_efn_booking_check_caller + idempotency_key/request_sha256 + "
    "FOR UPDATE settings outlet + hitung ulang ketersediaan; teks tamu difilter SEC-168",
    (
        "efn.table.booking",
        "efn_booking_cancel",
    ): "_efn_booking_check_caller + FOR UPDATE + hanya requested/confirmed belum mulai; ref asing -> not_found",
    (
        "efn.table.booking",
        "efn_booking_status",
    ): "_efn_booking_check_caller (baca; hanya ref outlet itu)",
    (
        "efn.table.booking",
        "action_efn_confirm",
    ): "grup POS manager + check_access(write) + company + state",
    (
        "efn.table.booking",
        "action_efn_seat",
    ): "grup POS manager + check_access(write) + company + state",
    (
        "efn.table.booking",
        "action_efn_done",
    ): "grup POS manager + check_access(write) + company + state",
    (
        "efn.table.booking",
        "action_efn_no_show",
    ): "grup POS manager + check_access(write) + company + state + lewat tahan",
    (
        "efn.table.booking",
        "action_efn_cancel",
    ): "grup POS manager + check_access(write) + company + state",
    # efn_online_slot (G23, W-36, docs/04 §15.4 path 20): `_efn_online_check_caller` kanal online (baca).
    (
        "efn.online.channel",
        "efn_online_slots",
    ): "_efn_online_check_caller (baca; kapasitas slot outlet)",
    # efn_online_transfer (G26, docs/04 §17.15 path #12 + UI Verifikator lewat web client/WireGuard).
    (
        "efn.online.order",
        "efn_online_transfer_submit",
    ): "G26: _efn_online_check_caller + FOR UPDATE + "
    "idempotency_key/proof_id/payload_sha256 + order bank_transfer placed belum kedaluwarsa + JPEG <= 1,5 MiB + sha256",
    (
        "efn.online.transfer.proof",
        "action_efn_open_decide",
    ): "grup group_payment_verifier + check_access(write) + record rule company; hanya bukti menunggu/kedaluwarsa",
    (
        "efn.online.transfer.decide",
        "action_confirm",
    ): "wizard -> _efn_decide: grup verifier + check_access(write) "
    "bukti + FOR UPDATE order + nominal mutasi = amount_due + alasan allowlist",
    # efn_promo (G27, docs/04 §18.1 path #22-#24 + tombol Pengelola Promo di web client).
    (
        "efn.promo.banner",
        "efn_promo_feed",
    ): "G27: _efn_promo_check_caller (grup BFF promo saja, satu company) + field whitelist",
    ("efn.promo.banner", "efn_promo_image"): "G27: _efn_promo_check_caller + kind allowlist + record company caller",
    (
        "efn.promo.popup",
        "efn_promo_push_claim",
    ): "G27: _efn_promo_check_caller + FOR UPDATE SKIP LOCKED queued->sent; tanpa data member",
    (
        "efn.promo.popup",
        "action_efn_queue_push",
    ): "G27: ACL write (Pengelola Promo) + record rule company + promo sedang tayang",
    # 3 metode POS (kasir/pelayan): `_efn_qr_check_pos_caller` = grup POS user + check_access(read) config + company
    # user + sesi POS opened; BFF dipanggil server-side (service key tidak ke browser); QR dirender server.
    (
        "pos.config",
        "efn_qr_session_open",
    ): "_efn_qr_check_pos_caller + efn_qr_enabled + meja ∈ denah + order draft "
    "meja; hanya id sesi BFF disimpan, token tidak disimpan/di-log",
    (
        "pos.config",
        "efn_qr_session_close",
    ): "_efn_qr_check_pos_caller + meja ∈ denah; tutup closed_by_staff + outbox",
    (
        "pos.order",
        "efn_qr_claim_send",
    ): "grup POS user + check_access(read) order + company user + order QR draft + "
    "mode auto + sesi POS opened; UPDATE ... WHERE claimed IS NULL (satu terminal menang)",
    # G28 (back-office demo penuh). Override CE (button_*, do_scrap, init, create/write) tidak didaftar (bukan metode
    # baru). Metode baru di bawah = aksi tombol form; semuanya check_access + record rule company standar.
    (
        "efn.demand.forecast",
        "action_efn_apply",
    ): "G28: grup stock.group_stock_manager + check_access(write) + record rule company; hanya state draft",
    (
        "efn.demand.forecast",
        "action_efn_reject",
    ): "G28: grup stock.group_stock_manager + check_access(write) + record rule company",
    ("efn.demand.forecast", "action_efn_recompute"): "G28: check_access(write) + record rule company; hanya draft",
    ("efn.forecast.run", "action_run"): "G28: wizard; check_access(create) efn.demand.forecast + gudang terbaca user",
    # G28: override metode CE (hak akses = ACL + record rule CE; logika tambahan setelah super()).
    ("stock.picking", "button_validate"): "override CE (efn_agri: tolak validasi transfer QC tanpa inspeksi selesai)",
    ("stock.scrap", "do_scrap"): "override CE (efn_ck_production: alasan waste wajib)",
    ("account.move", "button_cancel"): "override CE (efn_intercompany: batalkan tagihan draft antar-PT)",
    ("account.move", "button_draft"): "override CE (efn_intercompany: batalkan tagihan draft antar-PT)",
    ("purchase.order", "button_approve"): "override CE (efn_intercompany: SO di PT penjual lewat user teknis)",
    ("purchase.order", "button_cancel"): "override CE (efn_intercompany: batalkan SO draft antar-PT)",
    ("purchase.order", "button_confirm"): "override CE (efn_intercompany: harga transfer sebelum konfirmasi)",
    ("efn.ck.waste.report", "init"): "hook ORM init (buat SQL view); tanpa argumen, idempoten",
    ("efn.ck.yield.report", "init"): "hook ORM init (buat SQL view); tanpa argumen, idempoten",
    (
        "efn.agri.inspection",
        "action_done",
    ): "G28: grup efn_agri.group_agri_inspector + check_access(write) + record rule company; hanya draft",
    ("efn.agri.inspection", "action_cancel"): "G28: check_access(write) + record rule company; hanya draft",
    (
        "efn.agri.inspection",
        "action_apply_suggested_grade",
    ): "G28: write biasa (ACL + record rule) ke grade_id",
    ("efn.agri.harvest", "action_create_purchase"): "G28: check_access(write) + ACL create purchase.order user",
    ("efn.agri.farmer", "action_view_purchases"): "G28: hanya mengembalikan action (baca, ACL purchase)",
    (
        "stock.picking",
        "action_efn_agri_create_inspections",
    ): "G28: check_access(write) picking + ACL create efn.agri.inspection",
    ("stock.picking", "action_efn_agri_view_inspections"): "G28: hanya mengembalikan action (baca)",
    (
        "stock.warehouse",
        "action_efn_agri_enable_qc_gate",
    ): "G28: check_access(write) gudang (Inventory Administrator) + record rule company",
    (
        "efn.financial.report",
        "action_pdf",
    ): "G28: _efn_check_access (grup akuntansi baca + PT dalam company_ids user) sebelum data dibaca sudo",
    ("efn.financial.report", "action_xlsx"): "G28: sama dengan action_pdf",
}

MODEL_BASES = {"Model", "AbstractModel", "TransientModel"}


def _decorators(func):
    return {ast.unparse(dec) for dec in func.decorator_list}


def _class_models(cls_node):
    """(nama model, jenis base) kelas model Odoo, atau (None, None) untuk kelas biasa."""
    kind = None
    for base in cls_node.bases:
        text = ast.unparse(base)
        if text.split(".")[-1] in MODEL_BASES:
            kind = text.split(".")[-1]
    if not kind:
        return None, None
    names = []
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        if "_name" in targets and isinstance(stmt.value, ast.Constant):
            return [stmt.value.value], kind
        if "_inherit" in targets:
            value = ast.literal_eval(stmt.value)
            names = [value] if isinstance(value, str) else list(value)
    return names, kind


def scan_public_methods(addons_dir=ADDONS_DIR):
    """[(addon, file, model, method, kind)] metode publik non-``@api.private`` di model efn_*."""
    found = []
    for path in sorted(addons_dir.glob("efn_*/**/*.py")):
        rel = path.relative_to(addons_dir)
        if rel.parts[1] in ("tests", "migrations"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            models_, kind = _class_models(node)
            if not models_:
                continue
            for stmt in node.body:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if stmt.name.startswith("_") or "api.private" in _decorators(stmt):
                    continue
                for model in models_:
                    found.append((rel.parts[0], str(rel), model, stmt.name, kind))
    return found


@tagged("post_install", "-at_install", "efn")
class TestEfnRpcSurfaceStatic(BaseCase):
    """Tanpa database: murni scan AST sumber addon (cepat; juga gagal di CI sebelum install)."""

    def test_scanner_sees_addons(self):
        self.assertTrue((ADDONS_DIR / "efn_core" / "__manifest__.py").is_file())
        found = {(model, method) for _a, _f, model, method, _k in scan_public_methods()}
        # Sanity: scanner benar-benar menemukan metode publik yang diketahui.
        self.assertIn(("pos.config", "efn_otp_request"), found)

    def test_efn_abstract_models_have_no_public_methods(self):
        offenders = [
            f"{file}: {model}.{method}"
            for _addon, file, model, method, kind in scan_public_methods()
            if kind == "AbstractModel" and model.startswith("efn.")
        ]
        self.assertFalse(
            offenders,
            "AbstractModel efn.* tidak boleh punya metode publik (RPC-callable, SEC-58). "
            "Beri awalan '_' atau @api.private: " + ", ".join(offenders),
        )

    def test_public_methods_are_allowlisted(self):
        offenders = sorted(
            f"{file}: {model}.{method}"
            for _addon, file, model, method, _kind in scan_public_methods()
            if method not in ORM_OVERRIDES and (model, method) not in RPC_ALLOWLIST
        )
        self.assertFalse(
            offenders,
            "Metode publik baru di addon efn_* dapat dipanggil siapa pun lewat /web/dataset/call_kw "
            "dan /json/2. Jadikan privat ('_' / @api.private) atau tambahkan cek grup/sesi/company "
            "eksplisit lalu daftarkan di RPC_ALLOWLIST (review security): " + ", ".join(offenders),
        )

    def test_allowlist_has_no_stale_entries(self):
        found = {(model, method) for _a, _f, model, method, _k in scan_public_methods()}
        self.assertFalse(set(RPC_ALLOWLIST) - found, "Entri RPC_ALLOWLIST tanpa metode (hapus).")


@tagged("post_install", "-at_install", "efn")
class TestEfnRpcSurfaceRegistry(TransactionCase):
    def _efn_new_public(self, model_name):
        """Nama metode publik RPC-callable yang DIPERKENALKAN kelas efn_* (bukan override CE)."""
        cls = type(self.env[model_name])
        efn_names, other_names = set(), set()
        for klass in cls.__mro__[1:]:
            module = getattr(klass, "__module__", "") or ""
            target = efn_names if module.startswith("odoo.addons.efn_") else other_names
            for name, value in vars(klass).items():
                if not name.startswith("_") and inspect.isfunction(value):
                    target.add(name)
        result = set()
        for name in efn_names - other_names:
            try:
                get_public_method(self.env[model_name], name)
            except (AccessError, AttributeError):
                continue
            result.add(name)
        return result

    def test_bff_client_has_no_rpc_methods(self):
        self.assertFalse(self._efn_new_public("efn.bff.client"))
        for name in (
            "post",
            "get",
            "_efn_post",
            "_efn_get",
            "_efn_request",
            "_read_api_key",
        ):
            denied = False
            try:
                get_public_method(self.env["efn.bff.client"], name)
            except (AccessError, AttributeError):
                denied = True
            self.assertTrue(denied, f"efn.bff.client.{name} dapat dipanggil RPC")

    def test_installed_models_new_public_methods_allowlisted(self):
        offenders = []
        for model_name in self.env.registry:
            for method in self._efn_new_public(model_name):
                if (model_name, method) not in RPC_ALLOWLIST:
                    offenders.append(f"{model_name}.{method}")
        self.assertFalse(sorted(offenders), "Metode RPC publik efn_* di luar RPC_ALLOWLIST (SEC-58)")


@tagged("post_install", "-at_install", "efn")
class TestEfnBffClientRpcDenied(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login="efn_rpc_plain",
            password="efn_rpc_plain_pw",
            groups="base.group_user",
        )

    def setUp(self):
        super().setUp()
        base = "odoo.addons.efn_core.models.efn_bff_client.requests"
        self.http_post = patch(f"{base}.post").start()
        self.http_get = patch(f"{base}.get").start()
        self.addCleanup(patch.stopall)

    def _call_kw(self, method, args):
        response = self.url_open(
            "/web/dataset/call_kw",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "call",
                    "id": 1,
                    "params": {
                        "model": "efn.bff.client",
                        "method": method,
                        "args": args,
                        "kwargs": {},
                    },
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_call_kw_denied_for_internal_user(self):
        self.authenticate("efn_rpc_plain", "efn_rpc_plain_pw")
        earn = {
            "idempotency_key": "pos_earn:x",
            "order_ref": "x",
            "member_ref": "6281200000000",
        }
        for method, args in (
            ("post", ["/v1/pos/loyalty/earn", earn]),
            ("get", ["/v1/pos/privacy/erasures", {"store": "odoo"}]),
            ("_efn_post", ["/v1/pos/loyalty/earn", earn]),
            ("_efn_get", ["/v1/pos/privacy/erasures", {"store": "odoo"}]),
            ("_efn_request", ["POST", "/v1/pos/otp/request", earn]),
            ("_read_api_key", []),
        ):
            with self.subTest(method=method), mute_logger("odoo.http"):
                payload = self._call_kw(method, args)
                self.assertIn("error", payload, method)
                self.assertNotIn("result", payload)
        self.http_post.assert_not_called()
        self.http_get.assert_not_called()
