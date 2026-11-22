"""Konstanta & helper bersama efn_petty_cash (bukan model; tidak RPC-callable)."""

GROUP_USER = "efn_petty_cash.group_petty_cash_user"
GROUP_APPROVER = "efn_petty_cash.group_petty_cash_approver"
GROUP_FINANCE = "efn_petty_cash.group_petty_cash_finance"
GROUP_LIMIT_OVERRIDE = "efn_petty_cash.group_petty_cash_limit_override"

# Konteks penulisan sistem: hanya dihormati bersama env.su (lihat _efn_is_system_write). Konteks saja dapat dikirim
# lewat RPC, sehingga tidak pernah cukup sendirian.
SYSTEM_CTX = "efn_petty_cash_system"


def is_system_write(env):
    return bool(env.su and env.context.get(SYSTEM_CTX))


def is_recipient(user, partner):
    """SEC-G57-01: user = penerima dana (partner komersial sama), juga bila ``holder_user_id`` kosong."""
    return bool(partner) and user.partner_id.commercial_partner_id == partner.commercial_partner_id


def move_leg(currency, amount_cur, amount_comp):
    """Fragmen vals ``account.move.line`` untuk satu sisi pasangan seimbang.

    ``amount_cur`` bertanda dalam mata uang dokumen, ``amount_comp`` dalam mata uang company; positif = debit.
    ``currency_id`` selalu diisi (juga untuk mata uang company) agar rekonsiliasi valas dan selisih kurs benar.
    Pemanggil mengonversi SEKALI per pasangan dan memakai dua belahan angka yang sama (hindari selisih bulat).
    """
    return {"currency_id": currency.id, "amount_currency": amount_cur, "balance": amount_comp}


def convert(currency, company, amount, date):
    """Konversi ``amount`` dari ``currency`` ke mata uang company (dibulatkan)."""
    company_currency = company.currency_id
    if currency == company_currency:
        return company_currency.round(amount)
    return currency._convert(amount, company_currency, company, date)
