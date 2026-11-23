"""Data kunjungan visitor DEMO (V-01) untuk Insight "Pengunjung" — HANYA environment lokal (efn-dev / efn-prod gladi).

Keputusan pemilik proyek 23 Nov 2026: Insight Pengunjung SMD dan PB diisi data contoh agar terlihat saat demo.
Bukan data nyata. Semua baris ditandai `subject_key` berprefix `demo:` (kunci nyata = hex 64), `consented = 0`
sehingga TIDAK PERNAH masuk view segmen/kampanye (`v_seg_*` memfilter consented = 1).

Jalankan di container ETL (kredensial ClickHouse milik ETL, TLS; ADR-003: pengecualian operasional demo, bukan jalur API):
    docker exec -i efn-prod-etl-1 python - --days 14 < scripts/dev/seed_demo_visitors.py
Bersihkan (WAJIB sebelum gladi data nyata):
    docker exec -i efn-prod-etl-1 python - --cleanup --days 14 < scripts/dev/seed_demo_visitors.py

Cleanup menghapus baris fakta `demo:` lalu menghapus dan menghitung ulang agregat harian pada jendela itu dari fakta yang
tersisa (data nyata ikut dihitung ulang, tidak hilang).
"""

from __future__ import annotations

import argparse
import random
import uuid
from datetime import date, datetime, timedelta, timezone

from efn_etl.adapters import build_clickhouse_client
from efn_etl.behavior import AGG_STATEMENTS
from efn_etl.config import Settings

WIB = timezone(timedelta(hours=7))
DEMO_PREFIX = "demo:"

# company_id Odoo -> profil demo. Kode kanal = kode PT (docs/04 §12.1).
PROFILES = {
    "SMD": {
        "company_id": 11,
        "outlets": ["SMD01", "SMD02", "SMD03", "SMD04", "SMD05"],
        "visitors": (120, 200),
        "terms": ["telur", "beras 5kg", "minyak goreng", "susu uht", "daging sapi", "alpukat", "salmon", "roti",
                  "gula pasir", "kopi", "apel fuji", "ayam fillet"],
        "zero_terms": ["kimchi", "tempe organik", "oat milk", "matcha bubuk"],
    },
    "PB": {
        "company_id": 4,
        "outlets": ["P002", "P010", "P018", "P024", "P027"],
        "visitors": (80, 150),
        "terms": ["croissant", "roti tawar", "cheese cake", "tiramisu", "kopi", "sandwich", "donat", "baguette",
                  "red velvet", "milk bread"],
        "zero_terms": ["gluten free", "vegan cake", "kue ulang tahun custom"],
    },
}

COLUMNS = ["event_id", "subject_key", "subject_kind", "company_code", "channel", "channel_code", "outlet_code",
           "event_type", "product_id", "duration_s", "qty", "query", "result_count", "stock_status", "price_idr",
           "consented", "page", "received_at", "event_date", "ingested_at"]


def products(client, company_id: int) -> list[tuple[int, int]]:
    rows = client.query(
        "SELECT product_id, toInt64(list_price) FROM efn_dwh.dim_product FINAL "
        "WHERE company_id = {c:UInt32} AND active = 1 AND sale_ok = 1 AND available_in_pos = 1 AND list_price > 0 "
        "ORDER BY product_id LIMIT 400",
        parameters={"c": company_id},
    ).result_rows
    if not rows:
        raise SystemExit(f"dim_product kosong untuk company {company_id}")
    return [(int(p), int(price)) for p, price in rows]


def visitor_events(rng: random.Random, code: str, prof: dict, day: date, prods: list[tuple[int, int]], now: datetime):
    subject = DEMO_PREFIX + uuid.uuid4().hex
    channel = "web" if rng.random() < 0.55 else "app"
    outlet = rng.choice(prof["outlets"])
    t = datetime(day.year, day.month, day.day, rng.randint(7, 21), rng.randint(0, 59), tzinfo=WIB)
    out = []

    def ev(etype, page="", pid=0, price=None, dur=0, qty=0, query="", rc=None):
        nonlocal t
        t += timedelta(seconds=rng.randint(5, 90))
        out.append([uuid.uuid4(), subject, "device", code, channel, code, outlet, etype, pid, dur, qty, query, rc,
                    "in_stock" if pid else "", price, 0, page, t.astimezone(timezone.utc), day, now])

    ev("page_view", "home")
    if rng.random() < 0.15:
        ev("page_view", "promo")
    if rng.random() < 0.6:
        ev("page_view", "category")
    if rng.random() < 0.4:
        ev("page_view", "search")
        if rng.random() < 0.2:
            ev("search", query=rng.choice(prof["zero_terms"]), rc=0)
        else:
            ev("search", query=rng.choice(prof["terms"]), rc=rng.randint(1, 24))
    # popularitas produk condong (Zipf kasar): indeks kecil lebih sering
    viewed = {prods[min(int(rng.paretovariate(1.2)) - 1, len(prods) - 1)] for _ in range(rng.randint(1, 4))}
    for pid, price in viewed:
        ev("page_view", "product")
        ev("product_view", pid=pid, price=price, dur=rng.randint(4, 120))
    if rng.random() < 0.28:
        pid, price = rng.choice(sorted(viewed))
        ev("cart_add", pid=pid, price=price, qty=rng.randint(1, 3))
        ev("page_view", "cart")
        if rng.random() < 0.45:
            ev("page_view", "checkout")
            ev("checkout_start")
            if rng.random() < 0.65:
                ev("purchase", pid=pid, price=price, qty=1)
    return out


def recompute(client, start: date, end: date, k_min: int) -> None:
    for _name, sql in AGG_STATEMENTS:
        params = {"start": start, "end": end}
        if "{k:UInt32}" in sql:
            params["k"] = k_min
        client.command(sql, parameters=params)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--companies", default="SMD,PB")
    ap.add_argument("--seed", type=int, default=20261123)
    a = ap.parse_args()
    settings = Settings.from_env()
    client = build_clickhouse_client(settings)
    today = datetime.now(WIB).date()
    start, end = today - timedelta(days=a.days), today + timedelta(days=1)
    codes = [c for c in a.companies.split(",") if c in PROFILES]

    if a.cleanup:
        client.command("DELETE FROM efn_dwh.fact_visitor_event WHERE startsWith(subject_key, {p:String})",
                       parameters={"p": DEMO_PREFIX})
        for name, _sql in AGG_STATEMENTS:
            client.command(f"DELETE FROM efn_dwh.{name} WHERE day >= {{s:Date}} AND day < {{e:Date}}",
                           parameters={"s": start, "e": end})
        recompute(client, start, end, settings.behavior_k_min)
        print(f"cleanup selesai; agregat {start}..{end} dihitung ulang dari data nyata")
        return

    rng = random.Random(a.seed)
    now = datetime.now(timezone.utc)
    total = 0
    for code in codes:
        prof = PROFILES[code]
        prods = products(client, prof["company_id"])
        rng.shuffle(prods)
        rows = []
        for i in range(a.days + 1):
            day = start + timedelta(days=i)
            n = rng.randint(*prof["visitors"])
            if day.weekday() >= 5:
                n = int(n * 1.3)
            for _ in range(n):
                rows.extend(visitor_events(rng, code, prof, day, prods, now))
        client.insert("efn_dwh.fact_visitor_event", rows, column_names=COLUMNS)
        total += len(rows)
        print(f"{code}: {len(rows)} event demo")
    recompute(client, start, end, settings.behavior_k_min)
    print(f"total {total} event; agregat {start}..{end} dihitung")


if __name__ == "__main__":
    main()
