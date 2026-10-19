# Smoke E2E sisi Odoo untuk keuangan Poin EFN G21 (dev saja) — dijalankan lewat `odoo shell` oleh
# scripts/smoke/run-e2e-smoke.sh tahap `r` (docs/04 §13.1, L-61, L-66; modul efn_loyalty_accounting).
#
# Bukan modul Odoo dan bukan test unit. Langkah (env SMOKE_STEP):
#   - `policy`  : (1) user fiktif pembuat (Loyalty Manager) dan penyetuju (Accounting Manager) PT GL (idempoten);
#                 (2) pembuat membuat draft kebijakan GL dengan nilai acak yang BERBEDA dari default (redeem_min_points
#                 51..99, redeem_max_pct 41..49 — tetap di dalam plafon env BFF); (3) empat mata: pembuat TIDAK boleh
#                 menyetujui; (4) penyetuju menyetujui; (5) outbox dikirim sekarang (PUT bertanda tangan ke BFF);
#                 (6) membaca `GET /v1/pos/loyalty/params?company_code=GL` lewat pos.config GL (jalur yang dipakai POS).
#                 Keluaran: nilai yang diharapkan + nilai yang dibaca dari BFF + status outbox/kebijakan.
#   - `accounts`: pasang akun akrual PLACEHOLDER dev (SMOKE) di PT GL (idempoten).
#   - `accrual` : jalankan cron akrual SEKARANG (`_efn_run_all`, kemarin + lookback) untuk semua PT berkode; laporkan
#                 status per (PT, tanggal), jumlah jurnal `EFN-LOY-*`, dan baris log `EFN_LOYALTY_RECON` yang
#                 tertangkap (divalidasi: tepat 5 kunci kontrak). Dipanggil dua kali oleh run-e2e-smoke.sh untuk
#                 membuktikan idempotensi (jumlah jurnal tidak bertambah).
# Tidak ada secret di file ini; tidak ada data pribadi (user fiktif tanpa password; tidak ada nomor HP).
# Keluaran: satu baris `SMOKE_RESULT <json>`.
# ruff: noqa: F821  (variabel `env` disediakan odoo shell)
import json
import logging
import os
import random

from odoo import Command

if (os.environ.get("APP_ENV") or "").lower() != "dev":
    raise SystemExit("odoo_loyalty_accounting_smoke hanya untuk APP_ENV=dev")

STEP = os.environ["SMOKE_STEP"]
RECON_KEYS = {"bff_closing_liability_idr", "company_code", "date", "diff_idr", "odoo_liability_idr"}
LOYALTY_MANAGER = "efn_loyalty_accounting.group_loyalty_manager"
ACCOUNT_MANAGER = "account.group_account_manager"


def out(**kw):
    print("SMOKE_RESULT " + json.dumps(kw, default=str))


def gl_company():
    return env.ref("efn_setup_company.company_rtl")


def smoke_user(login, group_xmlids, company):
    Users = env["res.users"].sudo().with_context(no_reset_password=True)
    user = Users.search([("login", "=", login)], limit=1)
    if user:
        return user
    group_field = "group_ids" if "group_ids" in Users._fields else "groups_id"
    groups = [env.ref(x) for x in ("base.group_user", *group_xmlids)]
    return Users.create(
        {
            "name": f"SMOKE {login}",
            "login": login,
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            group_field: [Command.set([g.id for g in groups])],
        }
    )


def step_policy():
    company = gl_company()
    maker = smoke_user("smoke_loy_maker", [LOYALTY_MANAGER], company)
    approver = smoke_user("smoke_loy_approver", [ACCOUNT_MANAGER], company)
    env.cr.commit()
    want_min = random.randint(51, 99)  # noqa: S311 - nilai uji, bukan rahasia
    want_pct = random.randint(41, 49)  # noqa: S311
    Policy = env["efn.loyalty.policy"].with_company(company)
    policy = Policy.with_user(maker).create(
        {"company_id": company.id, "redeem_min_points": want_min, "redeem_max_pct": want_pct}
    )
    policy_id = policy.id
    env.cr.commit()  # draft tersimpan dulu: rollback percobaan terlarang di bawah tidak boleh menghapusnya
    four_eyes_blocked = False
    try:
        policy.with_user(maker).action_efn_approve()
    except Exception as exc:  # noqa: BLE001 - dilaporkan sebagai hasil smoke
        four_eyes_blocked = type(exc).__name__ in ("AccessError", "UserError")
        env.cr.rollback()
    policy = Policy.browse(policy_id)
    policy.with_user(approver).action_efn_approve()
    env.cr.commit()
    Outbox = env["efn.loyalty.policy.outbox"].sudo()
    Outbox._efn_process_due(limit=10)
    env.cr.commit()
    policy = Policy.sudo().browse(policy.id)
    outbox = policy.outbox_ids[:1]
    # Jalur POS: pos.config GL -> GET /v1/pos/loyalty/params?company_code=GL (cache per proses: shell baru = segar)
    config = env["pos.config"].sudo().search([("company_id", "=", company.id)], limit=1)
    params = config._efn_loyalty_params() if config else {}
    out(
        ok=True,
        version=policy.version,
        policy_state=policy.state,
        outbox_state=outbox.state,
        outbox_last_status=outbox.last_status,
        outbox_last_error=outbox.last_error or "",
        four_eyes_blocked=four_eyes_blocked,
        want_redeem_min_points=want_min,
        want_redeem_max_pct=want_pct,
        bff_redeem_min_points=params.get("redeem_min_points"),
        bff_redeem_max_pct=params.get("redeem_max_pct_of_order"),
        pos_config_found=bool(config),
    )


def find_or_create_account(company, code, name, account_type):
    Account = env["account.account"].sudo().with_company(company)
    account = Account.search([("code", "=", code), ("company_ids", "in", company.id)], limit=1)
    return account or Account.create(
        {"code": code, "name": name, "account_type": account_type, "company_ids": [Command.link(company.id)]}
    )


def step_accounts():
    company = gl_company()
    liability = find_or_create_account(company, "21990091", "Liabilitas / Kliring Poin EFN", "liability_current")
    expense = find_or_create_account(company, "SMKLY1", "SMOKE Beban Program Poin", "expense")
    clearing = find_or_create_account(company, "SMKLY2", "SMOKE Piutang Poin Antar-PT", "asset_current")
    payable = find_or_create_account(company, "SMKLY3", "SMOKE Utang Poin Antar-PT", "liability_current")
    company.sudo().write(
        {
            "efn_loyalty_liability_account_id": liability.id,
            "efn_loyalty_expense_account_id": expense.id,
            "efn_loyalty_clearing_account_id": clearing.id,
            "efn_loyalty_interco_payable_account_id": payable.id,
        }
    )
    env.cr.commit()
    out(ok=True, accounts=4)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def step_accrual():
    # PT lain (PB, BCR, ...) tanpa akun -> missing_config bila ada nilai; itu sah di dev dan dilaporkan apa adanya.
    recon_logger = logging.getLogger("odoo.addons.efn_loyalty_accounting.recon")
    old_level = recon_logger.level
    recon_logger.setLevel(logging.INFO)
    capture = _Capture()
    recon_logger.addHandler(capture)
    try:
        env["efn.loyalty.accrual"].sudo()._efn_run_all()
        env.cr.commit()
    finally:
        recon_logger.removeHandler(capture)
        recon_logger.setLevel(old_level)
    Accrual = env["efn.loyalty.accrual"].sudo()
    states = {}
    for record in Accrual.search([]):
        states.setdefault(record.state, 0)
        states[record.state] += 1
    bad = Accrual.search([("state", "=", "error")])
    recon_ok = True
    marker = "EFN_LOYALTY_RECON "
    for line in capture.lines:
        if not line.startswith(marker):
            continue
        try:
            payload = json.loads(line[len(marker) :])
        except ValueError:
            recon_ok = False
            continue
        if set(payload) != RECON_KEYS or any(
            not isinstance(payload[k], int) for k in ("bff_closing_liability_idr", "diff_idr", "odoo_liability_idr")
        ):
            recon_ok = False
    out(
        ok=True,
        states=states,
        error_codes=sorted({r.last_error or "" for r in bad}),
        moves=env["account.move"].sudo().search_count([("ref", "=like", "EFN-LOY-%")]),
        recon_lines=sum(1 for line in capture.lines if line.startswith(marker)),
        recon_format_ok=recon_ok,
        recon_mismatch=Accrual.search_count([("recon_status", "=", "mismatch")]),
    )


{
    "policy": step_policy,
    "accounts": step_accounts,
    "accrual": step_accrual,
}[STEP]()
