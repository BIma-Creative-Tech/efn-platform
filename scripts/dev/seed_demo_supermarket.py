# ruff: noqa: F821, E501, S311
# Jalankan (DB demo terpisah efn_smd_demo; lihat docs/runbooks/demo-supermarket-seed.md):
#   docker exec -i efn-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http \
#     < scripts/dev/seed_demo_supermarket.py
# Mode kecil (smoke test):
#   docker exec -i -e SMD_SEED_SKUS=40 -e SMD_SEED_DAYS=120 efn-odoo-1 odoo shell -c /etc/odoo/odoo.conf \
#     -d efn_smd_demo --no-http < scripts/dev/seed_demo_supermarket.py
"""G48 seed data simulasi supermarket PT Supermarket Demo (SMD). Idempoten, deterministik, tanpa PII nyata.

Prasyarat: DB dengan efn_setup_supermarket (DENGAN demo data), efn_expiry_monitor, point_of_sale (+ dependensinya).
Seed TIDAK memakai modul/data migrasi brand lama: histori penjualan = pos.order sintetis yang dibaca ETL apa adanya.
Semua record memakai company SMD dan xmlid demo efn_setup_supermarket (company, DC SMDDC, outlet SMD01..05, user
persona); tidak ada duplikat master. Penanda idempoten: default_code berprefiks ``SMD-`` (produk), ref ``SMD-SUP-``
(supplier) / ``SMD-MBR-`` (member), catatan ``[DEMO SUPERMARKET]`` (baris assortment, markdown, season, sesi POS
histori), nama lot deterministik, ``pos_reference`` order histori ``DEMO-<outlet>-<YYYYMMDD>-<n>``.

Tahap (setiap tahap di-commit; bila gagal, perbaiki lalu jalankan ulang: tahap yang sudah ada dilewati):
  1 supplier fiktif     2 SKU placeholder (lot + expiry, EAN-13 fiktif 299..., capability)   3 supplierinfo
  4 assortment (DSD, kapasitas rak) + jadwal supplier     5 kalender forecast + season v1 (PLACEHOLDER tanggal)
  6 stok awal lot (inventory adjustment)   7 markdown contoh   8 member fiktif   9 POS config outlet
  10 verifikasi user persona   11 histori penjualan = pos.order sintetis di sesi POS histori tertutup per outlet

Parameter env (default): SMD_SEED_SKUS=400, SMD_SEED_DAYS=730, SMD_SEED_HISTORY_END=hari ini (histori s/d H-1),
SMD_SEED_ORDERS_PER_DAY=1, SMD_SEED_HISTORY_SKUS=0 (0 = semua SKU), SMD_SEED_STOCK_SKUS=150, SMD_SEED_LOTS_MAX=3,
SMD_SEED_EXPIRED_LOTS=8, SMD_SEED_MARKDOWNS=12, SMD_SEED_MEMBERS=50, SMD_SEED_SHELF_SKUS=30, SMD_SEED_HISTORY=1,
SMD_SEED_POS=1.
"""

import calendar
import math
import os
import random
from datetime import date, datetime, time, timedelta
from time import monotonic

from odoo import Command, fields

TAG = "[DEMO SUPERMARKET]"
SEED = 4848
COMPANY_CODE = "SMD"
MODULE = "efn_setup_supermarket"
REQUIRED_MODULES = ("efn_setup_supermarket", "efn_expiry_monitor", "point_of_sale")
ORDER_REF_PREFIX = "DEMO-"
ORDER_HOUR_UTC = 5  # 12:00 WIB
OUTLET_SCALE = {"SMD01": 1.30, "SMD02": 1.00, "SMD03": 0.60, "SMD04": 1.15, "SMD05": 0.80}
WEEKDAY_FACTOR = (0.92, 0.90, 0.95, 0.97, 1.05, 1.25, 1.20)
TREND_PER_YEAR = 0.08
NOISE_SIGMA = 0.18
KG_PER_TRX = 0.6
BARCODE_PREFIX = "299"  # GS1 200-299 = restricted circulation (fiktif); hindari 13/15/17 (AI tanggal) & 21/22/23
CAPABILITY_XMLID = {
    "chiller": "efn_assortment.capability_chiller",
    "freezer": "efn_assortment.capability_freezer",
    "alcohol_license": "efn_assortment.capability_alcohol",
    "non_halal_allowed": "efn_assortment.capability_non_halal",
}

# kind: (kode, sufiks xmlid kategori, bobot dari 400, capability, umur simpan (min, maks) hari, use_time,
#        removal_time, harga satuan (min, maks), zona, delay supplier, MOQ, indeks supplier)
KIND_BASE = (
    ("veg", "VEG", "fresh_vegetable", 30, (), (5, 7), 1, 0, (6000, 20000), "fresh", 1, 5, 1),
    ("fruit", "FRU", "fresh_fruit", 25, (), (6, 10), 1, 0, (15000, 45000), "fresh", 1, 5, 2),
    ("meat", "MEA", "fresh_meat", 15, ("chiller",), (3, 4), 1, 0, (40000, 90000), "fresh", 1, 5, 3),
    ("fish", "FIS", "fresh_fish", 12, ("chiller",), (2, 3), 0, 0, (30000, 70000), "fresh", 1, 5, 4),
    ("bakery", "BAK", "fresh_bakery", 18, (), (3, 4), 1, 0, (8000, 45000), "fresh", 1, 10, 5),
    ("dairy", "DAI", "dairy_chilled", 40, ("chiller",), (14, 21), 3, 1, (8000, 60000), "chiller", 2, 12, 6),
    ("frozen", "FRZ", "frozen", 30, ("freezer",), (180, 365), 30, 7, (25000, 90000), "freezer", 3, 12, 7),
    ("dry", "DRY", "dry_grocery", 70, (), (365, 730), 60, 7, (5000, 120000), "dry", 5, 24, 8),
    ("bev", "BEV", "beverage", 45, (), (270, 540), 30, 7, (4000, 35000), "dry", 4, 24, 10),
    ("snack", "SNK", "snack", 40, (), (180, 300), 30, 7, (6000, 30000), "dry", 4, 24, 12),
    ("personal", "PER", "personal_care", 30, (), (730, 1095), 90, 14, (12000, 80000), "dry", 7, 12, 13),
    ("household", "HOU", "household", 25, (), (730, 1095), 90, 14, (10000, 70000), "dry", 7, 12, 13),
    ("alcohol", "ALC", "alcohol", 8, ("alcohol_license",), (730, 1460), 90, 14, (45000, 450000), "dry", 10, 12, 14),
    ("imported", "IMP", "imported_premium", 12, (), (180, 365), 30, 7, (60000, 350000), "dry", 30, 6, 15),
)
# Harga per kg (produk timbang "per kg").
KG_PRICE = {"veg": (12000, 45000), "fruit": (20000, 80000), "meat": (90000, 160000), "fish": (45000, 120000)}
# kind: (pengali basis, gajian, Ramadan, H-7..H-1 Lebaran, libur nasional, Natal/Tahun Baru, hari cakupan stok outlet)
KIND_DEMAND = {
    "veg": (1.6, 1.05, 1.10, 1.5, 1.05, 1.0, 2),
    "fruit": (1.4, 1.06, 1.30, 1.6, 1.05, 1.1, 2),
    "meat": (0.8, 1.08, 1.25, 2.1, 1.10, 1.1, 2),
    "fish": (0.6, 1.05, 1.05, 1.2, 1.05, 1.0, 2),
    "bakery": (1.5, 1.08, 0.90, 1.3, 1.10, 1.4, 2),
    "dairy": (1.2, 1.12, 1.10, 1.4, 1.05, 1.1, 4),
    "frozen": (0.6, 1.12, 1.15, 1.6, 1.10, 1.2, 10),
    "dry": (1.2, 1.15, 1.25, 1.9, 1.05, 1.1, 14),
    "bev": (1.3, 1.18, 1.35, 2.0, 1.10, 1.3, 14),
    "snack": (1.0, 1.18, 1.20, 2.3, 1.10, 1.3, 14),
    "personal": (0.5, 1.15, 1.05, 1.5, 1.00, 1.0, 14),
    "household": (0.5, 1.15, 1.05, 1.5, 1.00, 1.0, 14),
    "alcohol": (0.25, 1.20, 0.50, 0.5, 1.10, 1.8, 14),
    "imported": (0.15, 1.10, 1.10, 1.6, 1.05, 1.5, 14),
}
KIND_ORDER = tuple(row[0] for row in KIND_BASE)
KIND_SPEC = {}
for _row in KIND_BASE:
    _d = KIND_DEMAND[_row[0]]
    KIND_SPEC[_row[0]] = {
        "code": _row[1],
        "categ": _row[2],
        "weight": _row[3],
        "caps": _row[4],
        "life": _row[5],
        "use": _row[6],
        "removal": _row[7],
        "price": _row[8],
        "kg_price": KG_PRICE.get(_row[0], _row[8]),
        "zone": _row[9],
        "delay": _row[10],
        "moq": _row[11],
        "supplier": _row[12],
        "mult": _d[0],
        "payday": _d[1],
        "ramadan": _d[2],
        "lebaran": _d[3],
        "holiday": _d[4],
        "xmas": _d[5],
        "cover": _d[6],
    }
FRESH_KINDS = ("veg", "fruit", "meat", "fish", "bakery")
MIN_PER_KIND = {"alcohol": 2, "imported": 3}
ALTERNATE_KINDS = ("veg", "fruit")  # item ganjil: ukuran pak dulu -> separuh sayur/buah dijual per kg

# Katalog generik (tanpa merek). Format: "Nama" (ukuran default kind) atau "Nama=ukuran1/ukuran2".
CATALOG = {
    "veg": (
        ("per kg", "pak 250 g"),
        "Bayam;Kangkung;Sawi Hijau;Wortel;Kentang;Tomat;Cabai Merah Keriting;Cabai Rawit;Bawang Merah;Bawang Putih;"
        "Bawang Bombay;Kol;Brokoli;Buncis;Terong Ungu;Timun;Labu Siam;Jagung Manis;Selada;Daun Bawang;Seledri;Jahe;"
        "Kunyit;Lengkuas;Serai;Tauge;Pakcoy;Kacang Panjang;Paprika Merah;Jamur Tiram",
    ),
    "fruit": (
        ("per kg", "pak 500 g"),
        "Pisang Cavendish;Apel Merah;Jeruk Manis;Mangga Harum;Semangka;Melon;Pepaya;Nanas;Anggur Hijau;Pir;Salak;"
        "Kelengkeng;Jambu Kristal;Alpukat;Buah Naga;Stroberi;Kurma Segar;Lemon;Kiwi;Manggis;Rambutan;Sirsak;Markisa;"
        "Jeruk Nipis;Belimbing",
    ),
    "meat": (
        ("per kg",),
        "Daging Sapi Has Dalam;Daging Sapi Sengkel;Daging Sapi Giling;Iga Sapi;Ayam Utuh;Dada Ayam Fillet;Paha Ayam;"
        "Sayap Ayam;Ati Ampela Ayam;Daging Kambing;Tetelan Sapi;Buntut Sapi;Ayam Kampung;Bebek Utuh;Ceker Ayam",
    ),
    "fish": (
        ("per kg",),
        "Ikan Kembung;Ikan Tongkol;Ikan Bandeng;Ikan Nila;Ikan Lele;Udang Vaname;Cumi-cumi;Ikan Gurame;"
        "Ikan Kakap Fillet;Ikan Tenggiri;Kerang Hijau;Ikan Patin Fillet",
    ),
    "bakery": (
        ("satuan", "pak isi 4"),
        "Roti Tawar;Roti Gandum;Roti Sobek Cokelat;Roti Isi Keju;Donat Gula;Croissant Mentega;Bolu Pandan;Roti Kismis;"
        "Roti Burger;Roti Hotdog;Brownies Panggang;Bika Ambon;Lapis Legit;Roti Pisang;Kue Lumpur;Bolu Gulung;Roti Abon;"
        "Muffin Cokelat",
    ),
    "dairy": (
        ("kemasan kecil", "kemasan besar"),
        "Susu Segar Pasteurisasi=500 ml/1 L;Yoghurt Plain=150 g/500 g;Yoghurt Buah=150 g/500 g;"
        "Minuman Yoghurt=200 ml/700 ml;Keju Cheddar=165 g/500 g;Keju Mozzarella=200 g/500 g;Mentega Tawar=200 g/500 g;"
        "Krim Kental=200 ml/1 L;Telur Ayam=isi 10/isi 30;Telur Bebek=isi 6/isi 10;Tahu Putih=isi 5/isi 10;"
        "Tahu Kuning=isi 5/isi 10;Tempe=250 g/500 g;Sosis Ayam=200 g/500 g;Bakso Sapi=250 g/500 g;"
        "Puding Susu=isi 1/isi 4;Susu Kedelai=250 ml/1 L;Mi Basah=250 g/500 g;Kulit Lumpia=isi 10/isi 25;"
        "Santan Segar=200 ml/500 ml",
    ),
    "frozen": (
        ("500 g", "1 kg"),
        "Nugget Ayam;Sosis Sapi Beku;Kentang Goreng Beku;Dimsum Ayam;Siomay Ikan;Es Krim Vanila=700 ml/1,5 L;"
        "Es Krim Cokelat=700 ml/1,5 L;Bakso Ikan Beku;Udang Kupas Beku;Daging Burger Beku;Pizza Beku=isi 1/isi 2;"
        "Lumpia Beku;Edamame Beku;Fillet Ikan Dori Beku;Roti Paratha Beku=isi 5/isi 10",
    ),
    "dry": (
        ("500 g", "1 kg"),
        "Beras Premium=5 kg/10 kg;Beras Medium=5 kg/10 kg;Gula Pasir;Tepung Terigu;Minyak Goreng=1 L/2 L;"
        "Kecap Manis=275 ml/550 ml;Saus Sambal=135 ml/340 ml;Mi Instan Goreng=1 bks/5 bks;Mi Instan Kuah=1 bks/5 bks;"
        "Garam Beryodium=250 g/500 g;Kopi Bubuk=165 g/380 g;Teh Celup=25 kantong/50 kantong;"
        "Susu Kental Manis=370 g/490 g;Ikan Sarden Kaleng=155 g/425 g;Kornet Sapi=198 g/340 g;"
        "Santan Kemasan=65 ml/200 ml;Bumbu Instan Rendang=1 bks/3 bks;Bihun Jagung=175 g/350 g;Oat Instan=400 g/800 g;"
        "Sereal Jagung=275 g/500 g;Selai Kacang=170 g/340 g;Madu Murni=250 ml/500 ml;Tepung Beras;Tepung Tapioka;"
        "Gula Merah=250 g/500 g;Kacang Hijau=250 g/500 g;Susu Bubuk=400 g/800 g;Kaldu Bubuk=100 g/250 g;"
        "Saus Tomat=135 ml/340 ml;Mayones=100 g/300 g;Pasta Spageti=225 g/450 g;Agar-agar Bubuk=7 g/isi 3;"
        "Cokelat Bubuk=100 g/250 g;Kurma Kemasan;Kacang Tanah Kupas=250 g/500 g",
    ),
    "bev": (
        ("350 ml", "1 L"),
        "Air Mineral=600 ml/1,5 L;Air Mineral Galon=19 L/isi ulang 19 L;Teh Kemasan;Kopi Siap Minum=200 ml/1 L;"
        "Minuman Isotonik=500 ml/1 L;Jus Jeruk=250 ml/1 L;Jus Apel=250 ml/1 L;Susu UHT Cokelat=200 ml/1 L;"
        "Susu UHT Full Cream=200 ml/1 L;Minuman Soda Cola=330 ml/1,5 L;Minuman Soda Jeruk=330 ml/1,5 L;"
        "Sirup Rasa Mawar=460 ml/620 ml;Sirup Rasa Jeruk=460 ml/620 ml;Minuman Kelapa=330 ml/1 L;"
        "Minuman Energi=150 ml/250 ml;Teh Hijau Kemasan;Sari Kacang Hijau=250 ml/1 L;Minuman Lidah Buaya;"
        "Cokelat Bubuk Sachet=1 sachet/10 sachet;Kopi Instan Sachet=1 sachet/10 sachet;Es Batu Kristal=1 kg/5 kg;"
        "Minuman Jahe Instan=1 sachet/5 sachet;Minuman Yoghurt Ambient=200 ml/1 L",
    ),
    "snack": (
        ("70 g", "150 g"),
        "Keripik Kentang;Keripik Singkong;Kacang Atom;Biskuit Kelapa;Biskuit Cokelat;Wafer Vanila;"
        "Kue Kering Nastar=250 g/500 g;Kue Kering Kastengel=250 g/500 g;Astor Cokelat;Permen Mint;"
        "Cokelat Batang=40 g/100 g;Kerupuk Udang;Rengginang;Popcorn Karamel;Kacang Mete;Emping Melinjo;Biskuit Marie;"
        "Pilus Pedas;Makaroni Pedas;Rumput Laut Panggang",
    ),
    "personal": (
        ("kemasan kecil", "kemasan hemat"),
        "Sabun Mandi Batang;Sabun Mandi Cair;Sampo;Kondisioner;Pasta Gigi;Sikat Gigi=isi 1/isi 3;Deodoran;"
        "Losion Tubuh;Sabun Cuci Muka;Popok Bayi=isi 20/isi 50;Pembalut=isi 10/isi 20;"
        "Tisu Wajah=200 lembar/2 x 200 lembar;Minyak Kayu Putih=60 ml/120 ml;Bedak Bayi=100 g/200 g;"
        "Cotton Bud=isi 100/isi 200",
    ),
    "household": (
        ("kemasan kecil", "kemasan hemat"),
        "Deterjen Bubuk;Deterjen Cair;Pewangi Pakaian;Sabun Cuci Piring;Pembersih Lantai;Pembersih Kaca;"
        "Pemutih Pakaian;Kantong Sampah=isi 10/isi 30;Tisu Dapur=2 gulung/6 gulung;Obat Nyamuk Semprot;"
        "Spons Cuci=isi 1/isi 3;Kapur Barus;Pengharum Ruangan",
    ),
    "alcohol": (
        ("330 ml", "620 ml"),
        "Bir Lager;Bir Hitam;Anggur Merah Impor=750 ml;Anggur Putih Impor=750 ml;Sake=720 ml;Soju=360 ml",
    ),
    "imported": (
        ("kemasan",),
        "Keju Brie Impor=125 g;Sosis Babi Impor=250 g;Daging Sapi Wagyu Impor Beku=250 g;"
        "Minyak Zaitun Extra Virgin=500 ml;Keju Parmesan Impor=200 g;Bacon Babi Asap Impor=200 g;"
        "Salmon Atlantik Fillet Beku=250 g;Cokelat Batang Gaya Eropa=100 g;Selai Buah Impor=340 g;"
        "Pasta Gandum Impor=500 g;Teh Earl Grey Impor=isi 25;Biskuit Mentega Impor=454 g",
    ),
}
# Impor premium: syarat capability per produk (README efn_setup_supermarket §Capability).
IMPORTED_CAPS = {
    "Keju Brie Impor": ("chiller",),
    "Sosis Babi Impor": ("chiller", "non_halal_allowed"),
    "Daging Sapi Wagyu Impor Beku": ("freezer",),
    "Keju Parmesan Impor": ("chiller",),
    "Bacon Babi Asap Impor": ("chiller", "non_halal_allowed"),
    "Salmon Atlantik Fillet Beku": ("freezer",),
}
# Item musiman (pengali tambahan di atas faktor kategori).
ITEM_RAMADAN = {"Kurma Segar": 3.0, "Kurma Kemasan": 3.0, "Sirup Rasa Mawar": 2.5, "Sirup Rasa Jeruk": 2.0}
ITEM_PRE_LEBARAN = {"Kue Kering Nastar": 4.0, "Kue Kering Kastengel": 4.0, "Rengginang": 2.0, "Emping Melinjo": 2.0}
DSD_ITEMS = ("Air Mineral Galon", "Es Batu Kristal")

# Supplier fiktif: indeks -> nama (tanpa data pribadi).
SUPPLIERS = {
    1: "PT Contoh Sayur Segar Nusantara (FIKTIF)",
    2: "CV Contoh Buah Tropis (FIKTIF)",
    3: "PT Contoh Protein Hewani (FIKTIF)",
    4: "PT Contoh Hasil Laut (FIKTIF)",
    5: "PT Contoh Roti Harian (FIKTIF, DSD)",
    6: "PT Contoh Susu dan Olahan (FIKTIF)",
    7: "PT Contoh Pangan Beku (FIKTIF)",
    8: "PT Contoh Sembako Utama (FIKTIF)",
    9: "PT Contoh Distribusi Pangan Kering (FIKTIF)",
    10: "PT Contoh Minuman Ringan (FIKTIF)",
    11: "PT Contoh Air Minum Galon (FIKTIF, DSD)",
    12: "PT Contoh Makanan Ringan (FIKTIF)",
    13: "PT Contoh Perawatan Rumah dan Diri (FIKTIF)",
    14: "PT Contoh Minuman Beralkohol Berizin (FIKTIF)",
    15: "PT Contoh Importir Premium (FIKTIF)",
}
# Jadwal kunjungan supplier ke DC (bitmask Sen=1..Min=64): fresh setiap hari, beku Sel+Jum, kering Sen+Rab+Jum.
SUPPLIER_WEEKDAYS = {1: 127, 2: 127, 3: 127, 4: 127, 6: 63, 7: 18, 8: 21, 9: 21, 10: 21, 12: 21, 13: 5, 14: 1, 15: 1}
DSD_SUPPLIER_WEEKDAYS = {5: 127, 11: 42}

# Kalender (PLACEHOLDER: tanggal publik umum; wajib dikonfirmasi SKB/sidang isbat sebelum dipakai nyata).
RAMADAN = {
    2023: ("2023-03-23", "2023-04-21", "1444 H"),
    2024: ("2024-03-12", "2024-04-09", "1445 H"),
    2025: ("2025-03-01", "2025-03-30", "1446 H"),
    2026: ("2026-02-19", "2026-03-19", "1447 H"),
    2027: ("2027-02-08", "2027-03-09", "1448 H (perkiraan)"),
}
LEBARAN = {
    2023: ("2023-04-22", "2023-04-23"),
    2024: ("2024-04-10", "2024-04-11"),
    2025: ("2025-03-31", "2025-04-01"),
    2026: ("2026-03-20", "2026-03-21"),
    2027: ("2027-03-10", "2027-03-11"),
}
HOLIDAYS = (
    ("2024-01-01", "Tahun Baru Masehi"),
    ("2024-02-08", "Isra Mikraj"),
    ("2024-02-10", "Tahun Baru Imlek"),
    ("2024-03-11", "Hari Suci Nyepi"),
    ("2024-03-29", "Wafat Isa Almasih"),
    ("2024-03-31", "Hari Paskah"),
    ("2024-05-01", "Hari Buruh"),
    ("2024-05-09", "Kenaikan Isa Almasih"),
    ("2024-05-23", "Hari Raya Waisak"),
    ("2024-06-01", "Hari Lahir Pancasila"),
    ("2024-06-17", "Idul Adha"),
    ("2024-07-07", "Tahun Baru Islam"),
    ("2024-08-17", "Hari Kemerdekaan RI"),
    ("2024-09-16", "Maulid Nabi"),
    ("2024-12-25", "Hari Raya Natal"),
    ("2025-01-01", "Tahun Baru Masehi"),
    ("2025-01-27", "Isra Mikraj"),
    ("2025-01-29", "Tahun Baru Imlek"),
    ("2025-03-29", "Hari Suci Nyepi"),
    ("2025-04-18", "Wafat Isa Almasih"),
    ("2025-04-20", "Hari Paskah"),
    ("2025-05-01", "Hari Buruh"),
    ("2025-05-12", "Hari Raya Waisak"),
    ("2025-05-29", "Kenaikan Isa Almasih"),
    ("2025-06-01", "Hari Lahir Pancasila"),
    ("2025-06-06", "Idul Adha"),
    ("2025-06-27", "Tahun Baru Islam"),
    ("2025-08-17", "Hari Kemerdekaan RI"),
    ("2025-09-05", "Maulid Nabi"),
    ("2025-12-25", "Hari Raya Natal"),
    ("2026-01-01", "Tahun Baru Masehi"),
    ("2026-01-16", "Isra Mikraj"),
    ("2026-02-17", "Tahun Baru Imlek"),
    ("2026-03-19", "Hari Suci Nyepi"),
    ("2026-04-03", "Wafat Isa Almasih"),
    ("2026-04-05", "Hari Paskah"),
    ("2026-05-01", "Hari Buruh"),
    ("2026-05-14", "Kenaikan Isa Almasih"),
    ("2026-05-27", "Idul Adha"),
    ("2026-05-31", "Hari Raya Waisak"),
    ("2026-06-01", "Hari Lahir Pancasila"),
    ("2026-06-16", "Tahun Baru Islam"),
    ("2026-08-17", "Hari Kemerdekaan RI"),
    ("2026-08-25", "Maulid Nabi"),
    ("2026-12-25", "Hari Raya Natal"),
)
CUTI_BERSAMA = (
    "2024-12-26",
    "2025-01-28",
    "2025-03-28",
    "2025-04-02",
    "2025-04-03",
    "2025-04-04",
    "2025-04-07",
    "2025-05-13",
    "2025-05-30",
    "2025-06-09",
    "2025-12-26",
    "2026-02-16",
    "2026-03-18",
    "2026-03-23",
    "2026-03-24",
    "2026-05-15",
    "2026-05-28",
    "2026-12-24",
)

summary = {}


def say(msg):
    print("SEED", msg, flush=True)


def abort(msg):
    print("SEED ABORT", msg, flush=True)
    raise SystemExit(2)


def env_int(name, default, low, high):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(f"{name}={raw!r} bukan bilangan bulat")
    if not low <= value <= high:
        abort(f"{name}={value} di luar rentang {low}..{high}")
    return value


def iso(value):
    return date.fromisoformat(value)


def ean13(body12):
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


def round_price(value):
    return max(500, int(round(value / 100.0)) * 100)


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit = math.exp(-lam)
    k, p = 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def run_stage(label, func, *args):
    started = monotonic()
    say(f"== {label}: mulai")
    result = func(*args)
    env.cr.commit()
    say(f"== {label}: selesai ({monotonic() - started:.1f} s), commit")
    return result


# ---------------------------------------------------------------- rencana katalog (deterministik, tanpa DB)
def parse_catalog(kind):
    default_sizes, text = CATALOG[kind]
    items = []
    for raw in text.split(";"):
        entry = raw.strip()
        if not entry:
            continue
        if "=" in entry:
            name, sizes = entry.split("=", 1)
            items.append((name.strip(), tuple(s.strip() for s in sizes.split("/"))))
        else:
            items.append((entry, default_sizes))
    return items


def kind_names(kind, count):
    """[(item, nama lengkap)] sebanyak ``count``; urutan item dulu lalu ukuran, sisanya 'Varian N'."""
    items = parse_catalog(kind)
    names = []
    max_sizes = max(len(sizes) for _item, sizes in items)
    for pass_no in range(max_sizes):
        for idx, (item, sizes) in enumerate(items):
            if pass_no >= len(sizes):
                continue
            ordered = tuple(reversed(sizes)) if kind in ALTERNATE_KINDS and idx % 2 else sizes
            names.append((item, f"{item} {ordered[pass_no]}"))
    base = list(names)
    variant = 2
    while len(names) < count:
        names.extend((item, f"{name} Varian {variant}") for item, name in base)
        variant += 1
    return names[:count]


def allocate(n):
    total = sum(KIND_SPEC[k]["weight"] for k in KIND_ORDER)
    raw = {k: n * KIND_SPEC[k]["weight"] / total for k in KIND_ORDER}
    alloc = {k: max(MIN_PER_KIND.get(k, 1), int(raw[k])) for k in KIND_ORDER}
    remainder = sorted(KIND_ORDER, key=lambda k: raw[k] - int(raw[k]), reverse=True)
    i = 0
    while sum(alloc.values()) < n:
        alloc[remainder[i % len(remainder)]] += 1
        i += 1
    while sum(alloc.values()) > n:
        key = max(KIND_ORDER, key=lambda k: alloc[k] - MIN_PER_KIND.get(k, 1))
        alloc[key] -= 1
    return alloc


def build_plan(n_skus):
    alloc = allocate(n_skus)
    plan = []
    gidx = 0
    for kind_no, kind in enumerate(KIND_ORDER):
        spec = KIND_SPEC[kind]
        for seq, (item, name) in enumerate(kind_names(kind, alloc[kind]), start=1):
            gidx += 1
            code = f"SMD-{spec['code']}-{seq:04d}"
            rng = random.Random(f"{SEED}:sku:{code}")
            is_kg = "per kg" in name
            lo, hi = spec["kg_price"] if is_kg else spec["price"]
            price = round_price(rng.uniform(lo, hi))
            dsd = kind == "bakery" or item in DSD_ITEMS
            supplier = spec["supplier"]
            if item in DSD_ITEMS:
                supplier = 11
            elif kind == "dry" and seq % 2 == 0:
                supplier = 9
            plan.append(
                {
                    "code": code,
                    "kind": kind,
                    "item": item,
                    "name": name,
                    "is_kg": is_kg,
                    "gidx": gidx,
                    "barcode": ean13(f"{BARCODE_PREFIX}{kind_no:02d}{seq:07d}"),
                    "price": float(price),
                    "cost": float(round(price * rng.uniform(0.68, 0.82))),
                    "life": rng.randint(*spec["life"]),
                    "use": spec["use"],
                    "removal": spec["removal"],
                    "caps": IMPORTED_CAPS.get(item, spec["caps"]),
                    "dsd": dsd,
                    "supplier": supplier,
                }
            )
    order = list(range(len(plan)))
    random.Random(f"{SEED}:rank").shuffle(order)
    for rank, i in enumerate(order, start=1):
        info = plan[i]
        rng = random.Random(f"{SEED}:base:{info['code']}")
        base = 40.0 / rank**0.85 * KIND_SPEC[info["kind"]]["mult"]
        info["intermittent"] = rank > 0.3 * len(plan) and rng.random() < 0.25
        if info["intermittent"]:
            base *= 0.3
        if info["is_kg"]:
            base *= 0.35
        info["rank"] = rank
        info["base"] = base
    return plan


# ---------------------------------------------------------------- parameter & prasyarat
SKUS = env_int("SMD_SEED_SKUS", 400, 20, 2000)
DAYS = env_int("SMD_SEED_DAYS", 730, 28, 1100)
STOCK_SKUS = env_int("SMD_SEED_STOCK_SKUS", 150, 0, 2000)
LOTS_MAX = env_int("SMD_SEED_LOTS_MAX", 3, 1, 5)
EXPIRED_LOTS = env_int("SMD_SEED_EXPIRED_LOTS", 8, 0, 100)
MARKDOWNS = env_int("SMD_SEED_MARKDOWNS", 12, 0, 200)
MEMBERS = env_int("SMD_SEED_MEMBERS", 50, 0, 999)
SHELF_SKUS = env_int("SMD_SEED_SHELF_SKUS", 30, 0, 2000)
DO_HISTORY = env_int("SMD_SEED_HISTORY", 1, 0, 1)
DO_POS = env_int("SMD_SEED_POS", 1, 0, 1)
ORDERS_PER_DAY = env_int("SMD_SEED_ORDERS_PER_DAY", 1, 1, 50)
HISTORY_SKUS = env_int("SMD_SEED_HISTORY_SKUS", 0, 0, 2000)

_installed = set(
    env["ir.module.module"]
    .sudo()
    .search([("name", "in", list(REQUIRED_MODULES)), ("state", "=", "installed")])
    .mapped("name")
)
_missing = [m for m in REQUIRED_MODULES if m not in _installed]
if _missing:
    abort(f"modul belum terpasang: {', '.join(_missing)} (lihat docs/runbooks/demo-supermarket-seed.md)")
if not env.ref(f"{MODULE}.warehouse_smd01", raise_if_not_found=False):
    abort(f"data demo {MODULE} tidak ada (outlet SMD01). Buat DB dengan demo data lalu install ulang modulnya.")

_admin = env.ref("base.user_admin")
_company = env.ref(f"{MODULE}.company_smd")
if _company.efn_brand_code != COMPANY_CODE:
    abort(f"company {MODULE}.company_smd berkode {_company.efn_brand_code!r}, bukan {COMPANY_CODE}")
if _company not in _admin.company_ids:
    _admin.sudo().write({"company_ids": [Command.link(_company.id)]})
# Admin (bukan OdooBot): has_group() di stock (inventory_mode) & efn_* memeriksa env.user.
env = env(user=_admin.id, context=dict(env.context, allowed_company_ids=[_company.id], tz="Asia/Jakarta"))
admin = env.user
company = env.ref(f"{MODULE}.company_smd")
dc = env.ref(f"{MODULE}.warehouse_smd_dc")
outlets = [env.ref(f"{MODULE}.warehouse_smd0{i}") for i in range(1, 6)]
today = fields.Date.context_today(admin)
_end_raw = (os.environ.get("SMD_SEED_HISTORY_END") or "").strip()
try:
    hist_end = date.fromisoformat(_end_raw) if _end_raw else today  # eksklusif: histori berakhir H-1
except ValueError:
    abort(f"SMD_SEED_HISTORY_END={_end_raw!r} bukan YYYY-MM-DD")
if hist_end > today:
    abort(f"SMD_SEED_HISTORY_END={hist_end} di masa depan (order POS histori harus sebelum hari ini)")
hist_days = [hist_end - timedelta(days=DAYS - i) for i in range(DAYS)]
say(
    f"parameter: SKU={SKUS} hari={DAYS} histori {hist_days[0]}..{hist_days[-1]} order/hari={ORDERS_PER_DAY} "
    f"sku_histori={HISTORY_SKUS or 'semua'} stok_sku={STOCK_SKUS} lot_maks={LOTS_MAX} "
    f"lot_kedaluwarsa={EXPIRED_LOTS} markdown={MARKDOWNS} member={MEMBERS} rak={SHELF_SKUS} "
    f"histori={DO_HISTORY} pos={DO_POS}"
)
_outlet_codes = ", ".join(w.code for w in outlets)
say(f"company {company.name} id={company.id}; DC {dc.code}; outlet {_outlet_codes}; hari ini {today}")

categs = {k: env.ref(f"{MODULE}.product_category_smd_{KIND_SPEC[k]['categ']}") for k in KIND_ORDER}
thresholds = {}
for _kind, _categ in categs.items():
    if hasattr(_categ, "_efn_expiry_thresholds"):
        thresholds[_kind] = _categ._efn_expiry_thresholds()
    else:
        thresholds[_kind] = {"near": 0, "markdown": 0, "min_receipt": 0}
capabilities = {code: env.ref(xmlid) for code, xmlid in CAPABILITY_XMLID.items()}
plan = build_plan(SKUS)
meta = {info["code"]: info for info in plan}
say("alokasi SKU per kind: " + ", ".join(f"{k}={sum(1 for i in plan if i['kind'] == k)}" for k in KIND_ORDER))


# ---------------------------------------------------------------- tahap 1: supplier fiktif
def stage_suppliers():
    Partner = env["res.partner"].sudo().with_context(active_test=False)
    country = env.ref("base.id", raise_if_not_found=False)
    result, created = {}, 0
    for idx, name in SUPPLIERS.items():
        ref = f"SMD-SUP-{idx:02d}"
        partner = Partner.search([("ref", "=", ref), ("company_id", "=", company.id)], limit=1)
        if not partner:
            vals = {
                "name": name,
                "ref": ref,
                "is_company": True,
                "supplier_rank": 1,
                "company_id": company.id,
                "comment": f"{TAG} supplier fiktif, tanpa data kontak.",
            }
            if country:
                vals["country_id"] = country.id
            partner = Partner.create(vals)
            created += 1
        result[idx] = partner
    summary["supplier"] = f"{len(result)} (baru {created})"
    say(f"supplier: {len(result)} (baru {created})")
    return result


# ---------------------------------------------------------------- tahap 2: SKU placeholder
def stage_products():
    Template = env["product.template"].sudo().with_company(company)
    Product = env["product.product"].sudo().with_context(active_test=False)
    existing = {p.default_code for p in Product.search([("default_code", "in", list(meta))])}
    missing = [info for info in plan if info["code"] not in existing]
    taken = set()
    if missing:
        taken = set(Product.search([("barcode", "in", [i["barcode"] for i in missing])]).mapped("barcode"))
    kg = env.ref("uom.product_uom_kgm")
    unit = env.ref("uom.product_uom_unit")
    tfields = Template._fields
    vals_list = []
    for info in missing:
        uom = kg if info["is_kg"] else unit
        vals = {
            "name": info["name"],
            "default_code": info["code"],
            "categ_id": categs[info["kind"]].id,
            "type": "consu",
            "is_storable": True,
            "tracking": "lot",
            "use_expiration_date": True,
            "expiration_time": info["life"],
            "use_time": info["use"],
            "removal_time": info["removal"],
            "alert_time": thresholds[info["kind"]]["near"],
            "list_price": info["price"],
            "standard_price": info["cost"],
            "uom_id": uom.id,
            "company_id": company.id,
            "sale_ok": True,
            "purchase_ok": True,
            "description": f"<p>{TAG} SKU placeholder (bukan produk/merek nyata).</p>",
            "efn_required_capability_ids": [Command.set([capabilities[c].id for c in info["caps"]])],
        }
        if info["barcode"] not in taken:
            vals["barcode"] = info["barcode"]
        if "uom_po_id" in tfields:
            vals["uom_po_id"] = uom.id
        if "available_in_pos" in tfields:
            vals["available_in_pos"] = True
        if "to_weight" in tfields and info["is_kg"]:
            vals["to_weight"] = True
        vals_list.append(vals)
    for batch in chunks(vals_list, 100):
        Template.create(batch)
        say(f"produk: {len(batch)} template dibuat")
    products = {p.default_code: p for p in Product.search([("default_code", "in", list(meta))])}
    lost = [code for code in meta if code not in products]
    if lost:
        abort(f"produk tidak ditemukan setelah create: {lost[:5]}")
    if taken:
        say(f"PERINGATAN: {len(taken)} barcode sudah dipakai produk lain -> produk dibuat tanpa barcode")
    summary["produk"] = f"{len(products)} (baru {len(vals_list)}, kg {sum(1 for i in plan if i['is_kg'])})"
    say(f"produk: {len(products)} total, {len(vals_list)} baru")
    return products


# ---------------------------------------------------------------- tahap 3: supplierinfo
def stage_supplierinfo(products, suppliers):
    Seller = env["product.supplierinfo"].sudo()
    tmpl_ids = [products[code].product_tmpl_id.id for code in meta]
    existing = {(s.product_tmpl_id.id, s.partner_id.id) for s in Seller.search([("product_tmpl_id", "in", tmpl_ids)])}
    vals_list = []
    for info in plan:
        tmpl = products[info["code"]].product_tmpl_id
        spec = KIND_SPEC[info["kind"]]
        moq = 5 if info["is_kg"] else spec["moq"]
        rows = [(info["supplier"], info["cost"], moq, spec["delay"])]
        rng = random.Random(f"{SEED}:seller:{info['code']}")
        if info["kind"] in ("dry", "bev", "snack") and not info["dsd"] and rng.random() < 0.2:
            alt = 8 if info["supplier"] == 9 else 9
            rows.append((alt, float(round(info["cost"] * 0.97)), moq * 4, spec["delay"] + 3))
        for idx, price, min_qty, delay in rows:
            partner = suppliers[idx]
            if (tmpl.id, partner.id) in existing:
                continue
            vals_list.append(
                {
                    "product_tmpl_id": tmpl.id,
                    "partner_id": partner.id,
                    "price": price,
                    "min_qty": min_qty,
                    "delay": delay,
                    "company_id": company.id,
                }
            )
    for batch in chunks(vals_list, 200):
        Seller.create(batch)
    total = Seller.search_count([("product_tmpl_id", "in", tmpl_ids)])
    summary["supplierinfo"] = f"{total} (baru {len(vals_list)})"
    say(f"supplierinfo: {total} total, {len(vals_list)} baru")


# ---------------------------------------------------------------- tahap 4: assortment, matriks izin, jadwal supplier
def compute_allowance(products):
    """{(warehouse_id, code): 'ok' | kode alasan} lewat keputusan tunggal efn_assortment (docs/04 §28.5)."""
    Line = env["efn.assortment.line"]
    allowed = {}
    for wh in outlets:
        for code in meta:
            ok, reason = Line._efn_assortment_allowed(products[code], wh)
            allowed[(wh.id, code)] = "ok" if ok else reason
    return allowed


def is_sold(allowed, wh, code):
    # Produk DSD tetap dijual di outlet (hanya tidak dipasok DC).
    return allowed[(wh.id, code)] in ("ok", "dsd_only")


def stage_assortment(products, suppliers):
    Line = env["efn.assortment.line"].sudo()
    existing = Line.with_context(active_test=False).search(
        [("company_id", "=", company.id), ("note", "=like", f"{TAG}%")]
    )
    have = {(ln.warehouse_id.id, ln.product_tmpl_id.id, ln.categ_id.id, ln.supply_mode) for ln in existing}
    to_create = []

    def add(wh, supply_mode, note, tmpl=None, categ=None, partner=None, capacity=0.0):
        key = (wh.id, tmpl.id if tmpl else False, categ.id if categ else False, supply_mode)
        if key in have:
            return
        have.add(key)
        vals = {
            "company_id": company.id,
            "warehouse_id": wh.id,
            "mode": "allow",
            "supply_mode": supply_mode,
            "note": f"{TAG} {note}",
        }
        if tmpl:
            vals["product_tmpl_id"] = tmpl.id
        if categ:
            vals["categ_id"] = categ.id
        if partner:
            vals["dsd_partner_id"] = partner.id
        if capacity:
            vals["shelf_capacity_qty"] = capacity
        to_create.append(vals)

    # DSD: bakery (kategori) + galon/es batu (produk) dikirim supplier langsung ke semua outlet.
    for wh in outlets:
        add(wh, "dsd", "DSD roti harian", categ=categs["bakery"], partner=suppliers[5])
        for info in plan:
            if info["item"] in DSD_ITEMS:
                tmpl = products[info["code"]].product_tmpl_id
                add(wh, "dsd", "DSD air galon / es batu", tmpl=tmpl, partner=suppliers[11])
    if to_create:
        Line.create(to_create)
    dsd_created = len(to_create)
    to_create.clear()

    allowed = compute_allowance(products)
    # Kapasitas rak (S-16) untuk SKU terlaris yang dipasok DC, per outlet (baris produk, supply dc).
    top = sorted(plan, key=lambda i: i["rank"])
    for wh in outlets:
        scale = OUTLET_SCALE.get(wh.code, 1.0)
        picked = 0
        for info in top:
            if picked >= SHELF_SKUS:
                break
            if allowed[(wh.id, info["code"])] != "ok":
                continue
            daily = info["base"] * scale
            cover = KIND_SPEC[info["kind"]]["cover"]
            if info["is_kg"]:
                capacity = round(max(daily * cover * 1.5, 5.0), 1)
            else:
                capacity = float(max(6, round(daily * cover * 1.5)))
            tmpl = products[info["code"]].product_tmpl_id
            add(wh, "dc", "kapasitas rak (PLACEHOLDER planogram)", tmpl=tmpl, capacity=capacity)
            picked += 1
    if to_create:
        Line.create(to_create)
    cap_created = len(to_create)

    counts = {}
    for value in allowed.values():
        counts[value] = counts.get(value, 0) + 1
    for wh in outlets:
        sold = sum(1 for code in meta if is_sold(allowed, wh, code))
        reasons = {}
        for code in meta:
            value = allowed[(wh.id, code)]
            if value != "ok":
                reasons[value] = reasons.get(value, 0) + 1
        say(f"assortment {wh.code}: dijual {sold}/{len(meta)}; tidak lolos DC: {reasons or '-'}")
    summary["assortment_line_baru"] = f"DSD {dsd_created}, kapasitas rak {cap_created}"
    summary["matriks_izin"] = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))

    Sched = env["efn.supplier.schedule"].sudo()
    sched_new = 0
    for idx, mask in SUPPLIER_WEEKDAYS.items():
        partner = suppliers[idx]
        if Sched.search_count([("partner_id", "=", partner.id), ("warehouse_id", "=", dc.id)]):
            continue
        Sched.create(
            {
                "company_id": company.id,
                "partner_id": partner.id,
                "warehouse_id": dc.id,
                "weekdays": mask,
                "cutoff_time": 14.0,
                "tz": "Asia/Jakarta",
                "lead_days": 0,
            }
        )
        sched_new += 1
    for idx, mask in DSD_SUPPLIER_WEEKDAYS.items():
        partner = suppliers[idx]
        for wh in outlets:
            if Sched.search_count([("partner_id", "=", partner.id), ("warehouse_id", "=", wh.id)]):
                continue
            Sched.create(
                {
                    "company_id": company.id,
                    "partner_id": partner.id,
                    "warehouse_id": wh.id,
                    "weekdays": mask,
                    "cutoff_time": 20.0,
                    "tz": "Asia/Makassar" if wh.code == "SMD05" else "Asia/Jakarta",
                    "lead_days": 1,
                }
            )
            sched_new += 1
    summary["jadwal_supplier_baru"] = sched_new
    say(f"jadwal supplier baru: {sched_new}")
    return allowed


# ---------------------------------------------------------------- tahap 5: kalender forecast + season v1
def calendar_entries():
    entries = []
    for year, (start, end, hijri) in RAMADAN.items():
        day = iso(start)
        while day <= iso(end):
            entries.append((day, "ramadan", f"Ramadan {hijri} (DEMO, konfirmasi isbat)"))
            day += timedelta(days=1)
        first, second = LEBARAN[year]
        entries.append((iso(first), "holiday", f"Hari Raya Idul Fitri {hijri} hari ke-1 (DEMO)"))
        entries.append((iso(second), "holiday", f"Hari Raya Idul Fitri {hijri} hari ke-2 (DEMO)"))
        # lebaran_window = SATU tanggal (1 Syawal): worker mengambil libur pertama di dalam jendela manual; jendela
        # lebar bisa menangkap Nyepi (19 Mar 2026) sebagai 1 Syawal.
        entries.append((iso(first), "lebaran_window", f"1 Syawal {hijri} (DEMO, konfirmasi isbat)"))
    for day, name in HOLIDAYS:
        entries.append((iso(day), "holiday", f"{name} (DEMO, konfirmasi SKB)"))
    for day in CUTI_BERSAMA:
        entries.append((iso(day), "cuti_bersama", "Cuti bersama (DEMO, konfirmasi SKB)"))
    return [(day, kind, name[:64]) for day, kind, name in entries]


def stage_calendar():
    Calendar = env["efn.forecast.calendar"].sudo().with_context(active_test=False)
    have = {(r.date, r.kind, r.name) for r in Calendar.search([("company_id", "=", company.id)])}
    vals_list = []
    for day, kind, name in calendar_entries():
        if (day, kind, name) in have:
            continue
        have.add((day, kind, name))
        vals_list.append({"company_id": company.id, "date": day, "kind": kind, "name": name, "source": "manual"})
    for batch in chunks(vals_list, 200):
        Calendar.create(batch)
    Season = env["efn.forecast.season"].sudo().with_context(active_test=False)
    seasons = (
        (f"Ramadan (kalender) {TAG}", "ramadan", 0, 0, 1.20),
        (f"Pra-Lebaran H-7 s.d. H-1 {TAG}", "lebaran_window", 7, 0, 1.70),
        (f"Hari Lebaran H s.d. H+1 {TAG}", "lebaran_window", 0, 1, 0.40),
    )
    season_new = 0
    for name, calendar_kind, before, after, factor in seasons:
        if Season.search_count([("name", "=", name), ("company_id", "=", company.id)]):
            continue
        Season.create(
            {
                "name": name,
                "kind": "holiday",
                "calendar_kind": calendar_kind,
                "days_before": before,
                "days_after": after,
                "factor": factor,
                "company_id": company.id,
            }
        )
        season_new += 1
    total = Calendar.search_count([("company_id", "=", company.id)])
    summary["kalender"] = f"{total} entri SMD (baru {len(vals_list)}), season v1 baru {season_new}"
    say(f"kalender: {total} entri, baru {len(vals_list)}; season v1 baru {season_new}")


# ---------------------------------------------------------------- tahap 6: stok awal per lot
def zone_location(wh, info):
    kind = info["kind"]
    if kind in FRESH_KINDS:
        zone = "fresh"
    elif "freezer" in info["caps"]:
        zone = "freezer"
    elif "chiller" in info["caps"]:
        zone = "chiller"
    else:
        zone = KIND_SPEC[kind]["zone"]
    loc = env.ref(f"{MODULE}.location_{wh.code.lower()}_{zone}", raise_if_not_found=False)
    return loc or wh.lot_stock_id


def lot_left(rng, info, role, dc_lot=False):
    """Sisa hari sampai kedaluwarsa untuk peran lot (aman / near / markdown / expired)."""
    th = thresholds[info["kind"]]
    near, md, rem, life = th["near"], th["markdown"], info["removal"], info["life"]
    if role == "expired":
        return -rng.randint(1, 3)
    if role == "markdown":
        lo = rem + 1
        hi = max(md, lo)
    elif role == "near":
        lo = max(md, rem) + 1
        hi = max(near, lo)
    elif dc_lot:
        lo = max(near, th["min_receipt"]) + 1
        hi = max(life, lo)
    else:
        lo = near + 1
        hi = max(life, lo)
    return rng.randint(lo, hi)


def split_qty(info, total, parts):
    if info["is_kg"]:
        each = max(round(total / parts, 1), 0.5)
        return [each] * parts
    each = max(int(round(total / parts)), 1)
    return [float(each)] * parts


def stock_plan(allowed):
    rng = random.Random(f"{SEED}:stock")
    entries = []
    for info in plan:
        if info["dsd"]:
            continue  # DSD tidak lewat DC
        served = [w for w in outlets if allowed[(w.id, info["code"])] == "ok"]
        daily = sum(info["base"] * OUTLET_SCALE.get(w.code, 1.0) for w in served)
        days = 2 if info["kind"] in FRESH_KINDS else 10
        for k, qty in enumerate(split_qty(info, max(daily, 1.0) * days, 2), start=1):
            left = lot_left(rng, info, "safe", dc_lot=True)
            entries.append((dc, dc.lot_stock_id, info, f"{dc.code}-{info['gidx']:04d}-{k}", left, qty, "safe"))
    stock_codes = [i["code"] for i in sorted(plan, key=lambda i: i["rank"])][:STOCK_SKUS]
    pairs = []
    for wh in outlets:
        scale = OUTLET_SCALE.get(wh.code, 1.0)
        for code in stock_codes:
            if not is_sold(allowed, wh, code):
                continue
            info = meta[code]
            pairs.append((wh, code))
            th = thresholds[info["kind"]]
            total = info["base"] * scale * KIND_SPEC[info["kind"]]["cover"]
            n_lots = rng.randint(1, LOTS_MAX)
            for k, qty in enumerate(split_qty(info, max(total, 2.0), n_lots), start=1):
                role = "safe"
                if k == 1:
                    r = rng.random()
                    if r < 0.10 and th["near"] > max(th["markdown"], info["removal"]):
                        role = "near"
                    elif r < 0.22 and th["markdown"] > info["removal"]:
                        role = "markdown"
                left = lot_left(rng, info, role)
                name = f"{wh.code}-{info['gidx']:04d}-{k}"
                entries.append((wh, zone_location(wh, info), info, name, left, qty, role))
    if EXPIRED_LOTS and pairs:
        step = max(1, len(pairs) // EXPIRED_LOTS)
        for wh, code in pairs[::step][:EXPIRED_LOTS]:
            info = meta[code]
            name = f"{wh.code}-{info['gidx']:04d}-X"
            qty = 2.0 if not info["is_kg"] else 1.5
            entries.append((wh, zone_location(wh, info), info, name, lot_left(rng, info, "expired"), qty, "expired"))
    return entries


def stage_stock(products, allowed):
    entries = stock_plan(allowed)
    Lot = env["stock.lot"].sudo()
    product_ids = [p.id for p in products.values()]
    lots = {(lot.product_id.id, lot.name): lot for lot in Lot.search([("product_id", "in", product_ids)])}
    new_lots = []
    for _wh, _loc, info, name, left, _qty, _role in entries:
        product = products[info["code"]]
        if (product.id, name) in lots:
            continue
        expiry = datetime.combine(today + timedelta(days=left), time(5, 0))  # 12:00 WIB
        new_lots.append({"name": name, "product_id": product.id, "company_id": company.id, "expiration_date": expiry})
    for batch in chunks(new_lots, 500):
        for lot in Lot.create(batch):
            lots[(lot.product_id.id, lot.name)] = lot
    say(f"stok: {len(entries)} lot direncanakan, {len(new_lots)} lot baru")

    Quant = env["stock.quant"].sudo()
    lot_ids = [lot.id for lot in lots.values()]
    existing_quants = Quant.search([("lot_id", "in", lot_ids), ("location_id.usage", "=", "internal")])
    stocked = set(existing_quants.mapped("lot_id").ids)
    vals_list = []
    for _wh, loc, info, name, _left, qty, _role in entries:
        lot = lots[(products[info["code"]].id, name)]
        if lot.id in stocked:
            continue
        vals_list.append(
            {"product_id": lot.product_id.id, "location_id": loc.id, "lot_id": lot.id, "inventory_quantity": qty}
        )
    InvQuant = Quant.with_context(inventory_mode=True)
    applied = 0
    for batch in chunks(vals_list, 200):
        quants = InvQuant.create(batch)
        result = quants.action_apply_inventory()
        if isinstance(result, dict):
            say(f"PERINGATAN: action_apply_inventory -> wizard {result.get('res_model')} (tidak diterapkan)")
        else:
            applied += len(quants)
        say(f"stok: {applied}/{len(vals_list)} quant diterapkan")
    roles = {}
    for entry in entries:
        roles[entry[6]] = roles.get(entry[6], 0) + 1
    summary["stok"] = f"{len(entries)} lot (baru {len(new_lots)}), quant diterapkan {applied}; peran {roles}"
    outlet_lots = []
    for wh, _loc, info, name, left, qty, role in entries:
        if wh == dc:
            continue
        lot = lots[(products[info["code"]].id, name)]
        outlet_lots.append({"wh": wh, "info": info, "lot": lot, "left": left, "qty": qty, "role": role})
    return outlet_lots


# ---------------------------------------------------------------- tahap 7: markdown contoh (G45 efn.lot.markdown)
def stage_markdowns(outlet_lots):
    Markdown = env["efn.lot.markdown"].sudo()
    manager = env.ref(f"{MODULE}.user_smd_markdown_manager", raise_if_not_found=False)
    candidates = sorted(
        (e for e in outlet_lots if e["role"] in ("markdown", "near")),
        key=lambda e: (e["role"] != "markdown", e["wh"].code, e["info"]["code"]),
    )
    created = activated = 0
    for i, entry in enumerate(candidates[:MARKDOWNS]):
        lot, wh, info = entry["lot"], entry["wh"], entry["info"]
        if Markdown.search_count([("lot_id", "=", lot.id), ("warehouse_id", "=", wh.id)]):
            continue
        vals = {
            "company_id": company.id,
            "warehouse_id": wh.id,
            "lot_id": lot.id,
            "date_from": today,
            "date_to": today + timedelta(days=max(0, min(entry["left"] - 1, 3))),
            "qty_cap": entry["qty"] if i % 3 == 0 else 0.0,
            "notify_members": False,  # outbox kampanye = G46 (belum dibangun)
            "note": f"{TAG} contoh markdown lot near-expiry",
        }
        if (i // 2) % 2:
            vals["price"] = float(min(round_price(info["price"] * 0.7), info["price"] - 100))
        else:
            vals["discount_pct"] = 50.0 if info["kind"] in FRESH_KINDS else 30.0
        record = Markdown.create(vals)
        created += 1
        if i % 2:
            continue  # draft
        try:
            with env.cr.savepoint():
                if not manager:
                    raise ValueError("user demo markdown manager tidak ada")
                record.with_user(manager).with_company(company).action_efn_activate()
        except Exception as exc:
            say(f"markdown {lot.name}: aktivasi Markdown Manager gagal ({type(exc).__name__}: {exc}); sudo")
            record.write({"state": "active", "activated_by": admin.id, "activated_at": fields.Datetime.now()})
        activated += 1
    states = {}
    for rec in Markdown.search([("note", "=like", f"{TAG}%")]):
        states[rec.state] = states.get(rec.state, 0) + 1
    summary["markdown"] = f"baru {created} (aktif {activated}); status total {states}"
    say(f"markdown: baru {created}, aktif {activated}, kandidat {len(candidates)}; status {states}")


# ---------------------------------------------------------------- tahap 8: member fiktif (tanpa PII nyata)
def stage_members():
    Partner = env["res.partner"].sudo().with_context(active_test=False)
    have = set(Partner.search([("ref", "=like", "SMD-MBR-%")]).mapped("ref"))
    vals_list = []
    for i in range(1, MEMBERS + 1):
        ref = f"SMD-MBR-{i:03d}"
        if ref in have:
            continue
        vals_list.append(
            {
                "name": f"Member Fiktif {i:03d} (DEMO)",
                "ref": ref,
                # Pola +62 800-0000-xxxx: lolos format member_ref (^62[0-9]{8,13}$) tetapi bukan nomor seluler.
                "phone": f"+62 800-0000-{i:04d}",
                "company_id": company.id,
                "customer_rank": 1,
                "comment": f"<p>{TAG} member fiktif; nomor tidak dapat dihubungi.</p>",
            }
        )
    created = 0
    if vals_list:
        try:
            with env.cr.savepoint():
                created = len(Partner.create(vals_list))
        except Exception as exc:
            say(f"PERINGATAN member gagal dibuat ({type(exc).__name__}: {exc}); tahap dilewati")
    summary["member"] = f"{len(have) + created} (baru {created})"
    say(f"member: baru {created}, sudah ada {len(have)}")


# ---------------------------------------------------------------- tahap 9: POS config per outlet (opsional)
def stage_pos():
    Config = env["pos.config"].sudo().with_company(company)
    Method = env["pos.payment.method"].sudo().with_company(company)
    Journal = env["account.journal"].sudo().with_company(company)

    def cash_method(idx, wh):
        # CE: satu metode tunai (is_cash_count) hanya boleh dipakai satu pos.config -> jurnal + metode per outlet.
        mname = f"Tunai {wh.code} (DEMO)"
        method = Method.search([("name", "=", mname), ("company_id", "=", company.id)], limit=1)
        if method:
            return method
        code = f"SMC{idx}"
        journal = Journal.search([("code", "=", code), ("company_id", "=", company.id)], limit=1)
        if not journal:
            journal = Journal.create(
                {"name": f"Kas {wh.code} (DEMO)", "code": code, "type": "cash", "company_id": company.id}
            )
        return Method.create({"name": mname, "journal_id": journal.id, "company_id": company.id})

    methods = Method.browse()
    nomenclature = env.ref("efn_setup_pos.barcode_nomenclature_efn_retail", raise_if_not_found=False)
    created = 0
    for idx, wh in enumerate(outlets, start=1):
        name = f"Kasir {wh.code} (DEMO)"
        if Config.search_count([("name", "=", name), ("company_id", "=", company.id)]):
            continue
        vals = {"name": name, "company_id": company.id, "picking_type_id": wh.pos_type_id.id}
        try:
            with env.cr.savepoint():
                method = cash_method(idx, wh)
                methods |= method
                vals["payment_method_ids"] = [Command.set(method.ids)]
        except Exception as exc:
            say(f"PERINGATAN metode bayar tunai {wh.code} gagal ({type(exc).__name__}: {exc})")
        if nomenclature and "fallback_nomenclature_id" in Config._fields:
            vals["fallback_nomenclature_id"] = nomenclature.id
        try:
            with env.cr.savepoint():
                Config.create(vals)
                created += 1
        except Exception as exc:
            say(f"PERINGATAN pos.config {name} gagal ({type(exc).__name__}: {exc})")
    total = Config.search_count([("name", "=like", "Kasir SMD0%"), ("company_id", "=", company.id)])
    summary["pos_config"] = f"{total} (baru {created}, metode bayar {len(methods)})"
    say(f"pos.config: {total} (baru {created}); metode bayar SMD: {len(methods)}")


# ---------------------------------------------------------------- tahap 10: verifikasi user persona (tanpa ubah)
def stage_personas():
    issues = 0
    for i, wh in enumerate(outlets, start=1):
        user = env.ref(f"{MODULE}.user_smd0{i}_store_head", raise_if_not_found=False)
        if not user:
            say(f"persona kepala toko {wh.code}: TIDAK ADA")
            issues += 1
            continue
        user = user.sudo()
        scope_ok = user.efn_outlet_warehouse_ids == wh
        group_ok = user.has_group("efn_replenishment.group_replenishment_store")
        company_ok = company in user.company_ids
        if not (scope_ok and group_ok and company_ok):
            issues += 1
        say(
            f"persona {user.login}: outlet={','.join(user.efn_outlet_warehouse_ids.mapped('code')) or '-'} "
            f"scope={'OK' if scope_ok else 'SALAH'} grup={'OK' if group_ok else 'SALAH'} "
            f"company={'OK' if company_ok else 'SALAH'} (password tidak di-set skrip)"
        )
    for xmlid, group in (
        ("user_smd_buyer_dc", "efn_replenishment.group_replenishment_buyer"),
        ("user_smd_markdown_manager", "efn_expiry_monitor.group_markdown_manager"),
        ("user_smd_supervisor_ops", "efn_assortment.group_assortment_manager"),
    ):
        user = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        ok = bool(user) and user.sudo().has_group(group) and company in user.sudo().company_ids
        issues += 0 if ok else 1
        say(f"persona {xmlid}: {'OK' if ok else 'SALAH/TIDAK ADA'} ({group})")
    summary["persona"] = "OK" if not issues else f"{issues} masalah (lihat log, tidak diubah skrip)"


# ---------------------------------------------------------------- tahap 11: histori penjualan sintetis
def build_day_factors():
    ramadan = set()
    for start, end, _hijri in RAMADAN.values():
        day = iso(start)
        while day <= iso(end):
            ramadan.add(day)
            day += timedelta(days=1)
    lebaran = [iso(first) for first, _second in LEBARAN.values()]
    holidays = {iso(d) for d, _name in HOLIDAYS} | {iso(d) for pair in LEBARAN.values() for d in pair}
    cuti = {iso(d) for d in CUTI_BERSAMA}
    factors = {kind: [] for kind in KIND_ORDER}
    ram_flags, pre_flags = [], []
    for day in hist_days:
        weekday = WEEKDAY_FACTOR[day.weekday()]
        month_len = calendar.monthrange(day.year, day.month)[1]
        payday = day.day <= 7 or day.day > month_len - 7
        offset = min(((day - h).days for h in lebaran), key=abs)
        trend = 1.0 + TREND_PER_YEAR * (day - hist_end).days / 365.0
        in_ramadan = day in ramadan
        pre = -14 <= offset <= -1
        ram_flags.append(in_ramadan and not pre)
        pre_flags.append(pre)
        is_xmas = (day.month, day.day) in ((12, 24), (12, 25), (12, 31))
        for kind in KIND_ORDER:
            spec = KIND_SPEC[kind]
            f = weekday * trend
            if payday:
                f *= spec["payday"]
            if -7 <= offset <= -1:
                f *= spec["lebaran"]
            elif -14 <= offset <= -8:
                f *= 0.6 if kind == "alcohol" else 1.25
            elif offset in (0, 1):
                f *= 0.35
            elif 2 <= offset <= 6:
                f *= 0.75
            elif in_ramadan:
                f *= spec["ramadan"]
            if day in holidays and not (-1 <= offset <= 1):
                f *= spec["holiday"]
            elif day in cuti and not (-14 <= offset <= 6):
                f *= 1.05
            if is_xmas:
                f *= spec["xmas"]
            factors[kind].append(f)
    return factors, ram_flags, pre_flags


def synth_series(info, scale, rng, factors, ram_flags, pre_flags):
    """[(indeks hari, qty)] hanya hari qty > 0 (SKU intermittent -> banyak hari kosong, tidak ditulis)."""
    base = info["base"] * scale
    item_ram = ITEM_RAMADAN.get(info["item"], 1.0)
    item_pre = ITEM_PRE_LEBARAN.get(info["item"], 1.0)
    series = []
    for i, factor in enumerate(factors[info["kind"]]):
        lam = base * factor * math.exp(rng.gauss(0.0, NOISE_SIGMA))
        if ram_flags[i]:
            lam *= item_ram
        if pre_flags[i]:
            lam *= item_pre
        if info["is_kg"]:
            n = poisson(rng, lam / KG_PER_TRX)
            if n <= 0:
                continue
            qty = round(n * KG_PER_TRX * rng.uniform(0.8, 1.2), 3)
        else:
            qty = poisson(rng, lam)
            if qty <= 0:
                continue
        series.append((i, qty))
    return series


def history_configs():
    """{warehouse_id: pos.config} kasir demo per outlet (dibuat tahap 9)."""
    Config = env["pos.config"].sudo()
    configs = {}
    for wh in outlets:
        domain = [("name", "=", f"Kasir {wh.code} (DEMO)"), ("company_id", "=", company.id)]
        config = Config.search(domain, limit=1)
        if config:
            configs[wh.id] = config
    return configs


def history_session(config, wh):
    """Satu pos.session histori per outlet, state ``closed`` sejak dibuat: tidak dihitung sebagai sesi terbuka oleh
    ``_check_pos_config`` CE (``state != 'closed'``), jadi kasir tetap bisa membuka sesi baru. Akuntansi penutupan
    (jurnal, picking, statement) sengaja tidak dijalankan."""
    Session = env["pos.session"].sudo().with_context(onboarding_creation=True)
    name = f"{TAG} histori {wh.code}"
    start_at = datetime.combine(hist_days[0], time(0, 0)) - timedelta(hours=7)  # 00:00 WIB
    stop_at = datetime.combine(hist_days[-1], time(15, 0))  # 22:00 WIB
    session = Session.search([("config_id", "=", config.id), ("name", "=", name)], limit=1)
    if not session:
        session = Session.create(
            {
                "config_id": config.id,
                "user_id": admin.id,
                "name": name,
                "state": "closed",
                "start_at": start_at,
                "stop_at": stop_at,
            }
        )
    vals = {}
    if session.state != "closed":
        vals["state"] = "closed"
    if not session.start_at or session.start_at > start_at:
        vals["start_at"] = start_at
    if not session.stop_at or session.stop_at < stop_at:
        vals["stop_at"] = stop_at
    if vals:
        session.write(vals)
    return session


def make_unit_amounts(products):
    """Pajak per produk dihitung SEKALI (harga satuan), lalu dikalikan qty per baris."""
    cache = {}
    currency = company.currency_id

    def unit_amounts(code):
        if code not in cache:
            info = meta[code]
            product = products[code]
            taxes = product.taxes_id.filtered(lambda tax: tax.company_id == company)
            if taxes:
                res = taxes.compute_all(info["price"], currency, 1.0, product=product)
                cache[code] = (res["total_excluded"], res["total_included"], taxes.ids)
            else:
                cache[code] = (info["price"], info["price"], [])
        return cache[code]

    return unit_amounts


def outlet_daily_sales(wh, allowed, day_factors, codes):
    """{indeks hari: [(kode, qty)]} untuk outlet (pola permintaan sama untuk semua kanal histori)."""
    factors, ram_flags, pre_flags = day_factors
    scale = OUTLET_SCALE.get(wh.code, 1.0)
    by_day = {}
    for code in codes:
        if not is_sold(allowed, wh, code):
            continue
        rng = random.Random(f"{SEED}:hist:{code}:{wh.code}")
        for idx, qty in synth_series(meta[code], scale, rng, factors, ram_flags, pre_flags):
            by_day.setdefault(idx, []).append((code, qty))
    return by_day


def history_order_vals(wh, session, idx, n, items, products, unit_amounts):
    day = hist_days[idx]
    ref = f"{ORDER_REF_PREFIX}{wh.code}-{day:%Y%m%d}-{n}"
    lines = []
    total = tax = 0.0
    for code, qty in items:
        info = meta[code]
        excl, incl, tax_ids = unit_amounts(code)
        subtotal = round(excl * qty, 2)
        subtotal_incl = round(incl * qty, 2)
        total += subtotal_incl
        tax += subtotal_incl - subtotal
        lines.append(
            Command.create(
                {
                    "product_id": products[code].id,
                    "name": info["name"],
                    "full_product_name": info["name"],
                    "qty": qty,
                    "price_unit": info["price"],
                    "discount": 0.0,
                    "price_subtotal": subtotal,
                    "price_subtotal_incl": subtotal_incl,
                    "tax_ids": [Command.set(tax_ids)],
                    "total_cost": round(qty * info["cost"], 2),
                }
            )
        )
    total = round(total, 2)
    vals = {
        "name": ref,
        "pos_reference": ref,
        "session_id": session.id,
        "company_id": company.id,
        "date_order": datetime.combine(day, time(ORDER_HOUR_UTC, 0)) + timedelta(minutes=7 * (n - 1)),
        "state": "paid",
        "amount_total": total,
        "amount_tax": round(tax, 2),
        "amount_paid": total,
        "amount_return": 0.0,
        "lines": lines,
    }
    return ref, vals, len(lines)


def flush_orders(Order, batch, batch_lines, wh_code, month, totals):
    """Buat satu batch order (outlet x bulan) lalu commit + kosongkan cache ORM."""
    if not batch:
        return 0
    Order.create(batch)
    env.cr.commit()
    env.invalidate_all()
    totals["orders"] += len(batch)
    totals["lines"] += batch_lines
    label = f"{month[0]}-{month[1]:02d}" if month else "-"
    say(f"histori {wh_code} {label}: {len(batch)} order, {batch_lines} baris (total {totals['orders']} order)")
    return len(batch)


def stage_history(products, allowed):
    """Histori = pos.order ``paid`` di sesi histori tertutup (dibaca ETL ``_SQL_SALES_FACT`` apa adanya).

    Jalur ORM ``pos.order.create`` sudo langsung, BUKAN ``sync_from_ui``/``_process_order``: semua hook efn POS
    (loyalty/OTP/KDS/expiry/refund) ada di ``_process_order``/``_process_saved_order`` sehingga tidak terpicu; guard
    create ``efn_pos_refund_origin`` (state paid) melewati ``env.su``. Tanpa stock move, tanpa pos.payment."""
    configs = history_configs()
    missing = [w.code for w in outlets if w.id not in configs]
    if missing:
        say(f"PERINGATAN histori: pos.config demo tidak ada untuk {', '.join(missing)} (perlu SMD_SEED_POS=1)")
    codes = [i["code"] for i in sorted(plan, key=lambda i: i["rank"])]
    if HISTORY_SKUS:
        codes = codes[:HISTORY_SKUS]
    day_factors = build_day_factors()
    unit_amounts = make_unit_amounts(products)
    Order = env["pos.order"].sudo()
    domain = [("company_id", "=", company.id), ("pos_reference", "=like", f"{ORDER_REF_PREFIX}SMD%")]
    existing = {row["pos_reference"] for row in Order.search_read(domain, ["pos_reference"])}
    totals = {"orders": 0, "lines": 0, "skipped": 0}
    per_outlet = {}
    seq_field = "sequence_number" in Order._fields
    for wh in outlets:
        config = configs.get(wh.id)
        if not config:
            continue
        started = monotonic()
        session = history_session(config, wh)
        by_day = outlet_daily_sales(wh, allowed, day_factors, codes)
        batch, batch_lines, month = [], 0, None
        created = 0
        for idx in sorted(by_day):
            day = hist_days[idx]
            if month != (day.year, day.month):
                created += flush_orders(Order, batch, batch_lines, wh.code, month, totals)
                batch, batch_lines, month = [], 0, (day.year, day.month)
            items = by_day[idx]
            for n in range(1, ORDERS_PER_DAY + 1):
                part = items[n - 1 :: ORDERS_PER_DAY]
                if not part:
                    continue
                ref, vals, n_lines = history_order_vals(wh, session, idx, n, part, products, unit_amounts)
                if ref in existing:
                    totals["skipped"] += 1
                    continue
                if seq_field:
                    vals["sequence_number"] = idx * ORDERS_PER_DAY + n
                batch.append(vals)
                batch_lines += n_lines
        created += flush_orders(Order, batch, batch_lines, wh.code, month, totals)
        per_outlet[wh.code] = created
        say(f"histori {wh.code}: {created} order baru ({monotonic() - started:.1f} s), sesi {session.name}")
    summary["histori_pos_order"] = (
        f"order baru {totals['orders']}, baris baru {totals['lines']}, dilewati (sudah ada) {totals['skipped']}; "
        f"per outlet {per_outlet}; SKU histori {len(codes)}"
    )


# ---------------------------------------------------------------- tahap 12: pindai near-expiry (bila flag menyala)
def stage_expiry_scan():
    value = (env["ir.config_parameter"].sudo().get_param("efn_expiry_monitor.enabled") or "false").strip().lower()
    if value not in ("true", "1"):
        summary["expiry_scan"] = "dilewati (efn_expiry_monitor.enabled=false; nyalakan lalu jalankan cron, runbook)"
        say("pindai near-expiry dilewati: efn_expiry_monitor.enabled bukan true")
        return
    env["efn.expiry.alert"].sudo()._cron_efn_expiry_scan()
    total = env["efn.expiry.alert"].sudo().search_count([("company_id", "=", company.id)])
    summary["expiry_scan"] = f"{total} alert SMD"
    say(f"pindai near-expiry: {total} alert SMD")


# ---------------------------------------------------------------- eksekusi
_started = monotonic()
suppliers = run_stage("1 supplier", stage_suppliers)
products = run_stage("2 produk", stage_products)
run_stage("3 supplierinfo", stage_supplierinfo, products, suppliers)
allowed = run_stage("4 assortment + jadwal supplier", stage_assortment, products, suppliers)
run_stage("5 kalender + season", stage_calendar)
outlet_lots = run_stage("6 stok awal", stage_stock, products, allowed)
run_stage("7 markdown", stage_markdowns, outlet_lots)
run_stage("8 member", stage_members)
if DO_POS:
    run_stage("9 pos.config", stage_pos)
run_stage("10 verifikasi persona", stage_personas)
if DO_HISTORY:
    run_stage("11 histori penjualan (pos.order)", stage_history, products, allowed)
else:
    summary["histori"] = "dilewati (SMD_SEED_HISTORY=0)"
run_stage("12 pindai near-expiry", stage_expiry_scan)
say(f"== RINGKASAN ({monotonic() - _started:.1f} s)")
for _key, _value in summary.items():
    say(f"  {_key}: {_value}")
print("SEED_DONE", flush=True)
