/**
 * Glosarium istilah (Bahasa Indonesia): definisi singkat + cara membaca. Satu sumber untuk komponen <Term>/<TermHints>
 * (tooltip/popover di label KPI, judul kartu, header kolom) dan halaman /glosarium. Murni (tanpa I/O), dapat diuji.
 *
 * `match` = pola deteksi di teks label (huruf besar-kecil dibedakan untuk singkatan agar "FIN" tidak cocok dengan
 * "final", dan "CK" tidak cocok dengan "ck" di tengah kata). Pola dikompilasi sekali di bawah.
 */
export interface GlossaryEntry {
  id: string;
  /** Nama tampil. */
  term: string;
  /** Kepanjangan / padanan. */
  full?: string;
  /** Definisi satu-dua kalimat. */
  def: string;
  /** Cara membaca angkanya. */
  read?: string;
  /** Sumber pola deteksi (RegExp source). */
  match: string;
  /** Peka huruf besar-kecil (singkatan). Bawaan false. */
  caseSensitive?: boolean;
}

export const GLOSSARY: readonly GlossaryEntry[] = [
  {
    id: 'wape',
    term: 'WAPE',
    full: 'Weighted Absolute Percentage Error',
    def: 'Rata-rata kesalahan forecast terhadap total aktual: jumlah selisih mutlak forecast dan aktual dibagi total aktual.',
    read: '20% berarti forecast meleset sekitar 20% dari volume aktual. Makin kecil makin baik.',
    match: '\\bWAPE\\b',
    caseSensitive: true,
  },
  {
    id: 'p10-p90',
    term: 'p10 - p90',
    full: 'Persentil ke-10 dan ke-90',
    def: 'Rentang 80% kemungkinan dari forecast: batas bawah (p10) dan batas atas (p90).',
    read: 'Aktual diperkirakan jatuh di antara keduanya pada sekitar 8 dari 10 hari. Rentang lebar = prediksi kurang pasti.',
    match: '\\bp(?:10|90)\\b',
  },
  {
    id: 'bias',
    term: 'Bias forecast',
    def: 'Arah kesalahan forecast: selisih forecast dikurangi aktual dibagi total aktual.',
    read: 'Positif = forecast cenderung terlalu tinggi (risiko stok menumpuk). Negatif = terlalu rendah (risiko stok habis). Mendekati 0% = seimbang.',
    match: '\\bbias\\b',
  },
  {
    id: 'mdr',
    term: 'MDR',
    full: 'Merchant Discount Rate',
    def: 'Biaya yang dipotong penyedia pembayaran (bank/e-wallet) dari setiap transaksi non-tunai.',
    read: 'MDR % = total MDR dibagi nilai pembayaran. Lebih tinggi berarti biaya pembayaran lebih mahal.',
    match: '\\bMDR\\b',
    caseSensitive: true,
  },
  {
    id: 'hpp',
    term: 'HPP',
    full: 'Harga Pokok Penjualan',
    def: 'Biaya barang yang terjual (bahan dan produksi), sebelum biaya operasional.',
    read: 'HPP % = HPP dibagi penjualan bersih. Makin kecil makin besar margin kotor.',
    match: '\\bHPP\\b',
    caseSensitive: true,
  },
  {
    id: 'sell-through',
    term: 'Sell-through',
    def: 'Berapa bagian pasokan toko yang terjual: terjual dibagi pasokan (diterima dari CK dan sumber lain + produksi toko + transfer masuk - transfer keluar).',
    read: '85% ke atas baik, 70-85% perlu dicek, di bawah 70% bermasalah (ambang proposal). Berbasis arus barang, bukan stok: periode pendek bisa melebihi 100%.',
    match: 'sell-?through',
  },
  {
    id: 'oos',
    term: 'OOS',
    full: 'Out of Stock',
    def: 'Barang habis di rak/stok saat dibutuhkan.',
    read: 'OOS rate = persentase SKU-hari yang kosong. Makin kecil makin baik; tinggi berarti penjualan hilang.',
    match: '\\bOOS\\b',
    caseSensitive: true,
  },
  {
    id: 'sku',
    term: 'SKU',
    full: 'Stock Keeping Unit',
    def: 'Satu varian produk yang dilacak stoknya (kode produk unik).',
    read: '"SKU dievaluasi" = jumlah produk yang masuk perhitungan.',
    match: '\\bSKU\\b',
    caseSensitive: true,
  },
  {
    id: 'dc',
    term: 'DC',
    full: 'Distribution Center',
    def: 'Gudang pusat supermarket yang menerima barang dari pemasok dan mengirimnya ke outlet.',
    match: '\\bDC\\b',
    caseSensitive: true,
  },
  {
    id: 'fefo',
    term: 'FEFO',
    full: 'First Expired, First Out',
    def: 'Aturan pengeluaran stok: lot yang paling dulu kedaluwarsa dikeluarkan lebih dulu.',
    read: 'Dipakai untuk menentukan lot mana yang harus dijual/dikirim lebih dulu agar tidak kedaluwarsa.',
    match: '\\bFEFO\\b',
    caseSensitive: true,
  },
  {
    id: 'yoy',
    term: 'YoY',
    full: 'Year over Year',
    def: 'Perbandingan dengan periode yang sama tahun lalu.',
    read: '+10% YoY berarti 10% lebih tinggi dibanding tanggal yang sama setahun lalu.',
    match: '\\bYoY\\b',
    caseSensitive: true,
  },
  {
    id: 'mtd',
    term: 'MTD',
    full: 'Month to Date',
    def: 'Akumulasi dari awal bulan berjalan sampai kemarin.',
    match: '\\bMTD\\b',
    caseSensitive: true,
  },
  {
    id: 'fin',
    term: 'FIN',
    full: 'Finished goods (produk jadi)',
    def: 'Produk jadi yang siap dijual di toko, satuan EA. Bahan baku, WIP, kemasan, dan supplies bukan FIN.',
    read: 'Angka "Hanya produk jadi (FIN)" tidak memuat barang lain yang juga dikirim CK.',
    match: '\\bFIN\\b',
    caseSensitive: true,
  },
  {
    id: 'wip',
    term: 'WIP',
    full: 'Work in Process (barang setengah jadi)',
    def: 'Barang setengah jadi dari CK yang diolah lagi di toko menjadi produk jadi (mis. adonan untuk dipanggang).',
    read: 'Produksi toko (FINS/FINK) berasal dari WIP dan bahan baku yang dikirim CK.',
    match: '\\bWIP\\b',
    caseSensitive: true,
  },
  {
    id: 'ck',
    term: 'CK',
    full: 'Central Kitchen',
    def: 'Dapur pusat yang memproduksi dan mengirim barang ke toko.',
    read: 'Cabang berjenis CK tidak menjual ke pelanggan, jadi sell-through-nya n/a.',
    match: '\\bCK\\b',
    caseSensitive: true,
  },
  {
    id: 'fill-rate',
    term: 'Fill rate',
    def: 'Seberapa penuh pesanan terpenuhi: qty yang benar-benar dikirim atau diterima dibanding qty usulan yang disetujui.',
    read: 'Makin dekat 100% makin baik. Di bawah ambang berarti ada kurang kirim.',
    // G67: label F&B "Fill rate outlet (baris)" memakai entri `fill-rate-line`; definisi entri ini (usulan DC SMD) tidak diubah,
    // dan label SMD ("Fill rate baris", "Fill rate qty", "Fill rate per outlet") tetap memakainya.
    match: 'fill ?rate(?! outlet)',
  },
  {
    id: 'gmroi',
    term: 'GMROI',
    full: 'Gross Margin Return on Inventory',
    def: 'Laba kotor yang dihasilkan per rupiah nilai persediaan rata-rata.',
    read: '2,0 berarti tiap Rp1 persediaan menghasilkan Rp2 laba kotor dalam periode itu. Makin besar makin baik.',
    match: '\\bGMROI\\b',
    caseSensitive: true,
  },
  // G66 (modul Production Order, docs/04 §30).
  {
    id: 'kemasan-utuh',
    term: 'Kemasan utuh',
    def: 'Bahan yang hanya bisa diambil per kemasan penuh (mis. susu 1.000 ML). Kebutuhan dibulatkan ke atas ke kelipatan isi kemasan.',
    read: 'Kebutuhan 1.950 ML dari kemasan 1.000 ML berarti 2 kemasan (kebutuhan efektif 2.000 ML), sisa terbuka 50 ML.',
    match: 'kemasan utuh',
  },
  {
    id: 'sisa-terbuka',
    term: 'Sisa terbuka',
    def: 'Bagian kemasan utuh yang sudah dibuka tetapi tidak terpakai oleh rencana ini (kemasan dikali isi dikurangi kebutuhan).',
    read: 'Sisa ini hanya bertahan selama umur setelah dibuka; bila cukup untuk sedikitnya satu unit produk, sistem mengusulkan batch tambahan.',
    match: 'sisa terbuka',
  },
  {
    id: 'usulan-batch',
    term: 'Usulan batch',
    full: 'Usulan batch tambahan',
    def: 'Alternatif jumlah produksi tambahan agar sisa kemasan terbuka terpakai, dihitung dari bahan yang tersisa dibagi pemakaian per unit.',
    read: 'Usulan bersifat pilihan: planner menerima atau menolak. Menerima satu usulan menggugurkan usulan lain untuk bahan yang sama dan mewajibkan hitung ulang; permintaan asal tidak berkurang.',
    match: 'usulan batch',
  },
  {
    id: 'outbox',
    term: 'Outbox',
    full: 'Antrean dokumen keluar ke ESB',
    def: 'Daftar dokumen ESB (transfer, produksi) yang dibuat dari rencana. Tiap dokumen dikirim satu per satu dan statusnya dipantau sampai terkonfirmasi.',
    read: 'Hasil belum pasti berarti ESB tidak menjawab setelah dokumen terkirim: sistem mencocokkan ulang, tidak mengirim ganda. Gagal berarti perlu tindakan manusia.',
    match: '\\boutbox\\b',
  },
  {
    id: 'posting',
    term: 'Posting',
    def: 'Mengirim dokumen transfer dan produksi dari rencana yang sudah disetujui menjadi dokumen sungguhan di ESB.',
    read: 'Dilakukan dua fase (transfer, lalu produksi). Dokumen produksi langsung mengubah stok ESB, karena itu butuh pratinjau dan konfirmasi eksplisit.',
    match: '\\bposting\\b',
  },
  // G67 (Laba Rugi Outlet, Waste & Pemakaian Internal, Fill Rate Outlet ke CK; docs/04 §31.10).
  {
    id: 'laba-kotor',
    term: 'Laba kotor',
    def: 'Penjualan bersih dikurangi harga pokok penjualan, sesuai laporan laba rugi ESB (tanpa penyesuaian EFN).',
    read: 'Persen laba kotor = laba kotor dibagi penjualan bersih. Angka dan tandanya ditampilkan apa adanya dari laporan ESB.',
    match: 'laba kotor|gross profit',
  },
  {
    id: 'ebitda',
    term: 'EBITDA',
    full: 'Earnings Before Interest, Taxes, Depreciation, and Amortization',
    def: 'Laba operasional sebelum bunga, pajak, penyusutan, dan amortisasi, sesuai laporan laba rugi ESB.',
    read: 'Dipakai membandingkan kemampuan outlet menghasilkan laba dari operasional. Persen EBITDA = EBITDA dibagi penjualan bersih.',
    match: '\\bEBITDA\\b',
    caseSensitive: true,
  },
  {
    id: 'laba-bersih',
    term: 'Laba bersih',
    def: 'Laba setelah seluruh biaya menurut laporan laba rugi ESB. Bulan berjalan masih sementara dan bulan tertutup dapat berubah karena jurnal susulan.',
    read: 'Persen laba bersih = laba bersih dibagi penjualan bersih. Tanda negatif berarti rugi.',
    match: 'laba bersih|net profit',
  },
  {
    id: 'tidak-teralokasi',
    term: 'Tidak teralokasi ke outlet',
    def: 'Selisih total brand dikurangi jumlah seluruh outlet pada pos yang sama: biaya atau pendapatan tingkat pusat yang tidak dibebankan ke outlet.',
    read: 'Selisih nol atau kecil berarti seluruh pos sudah teralokasi ke outlet. Selisih besar berarti ada biaya pusat di luar angka outlet.',
    match: 'tidak teralokasi',
  },
  {
    id: 'waste',
    term: 'Waste',
    def: 'Barang yang dibuang karena rusak, kedaluwarsa, atau tidak layak jual; nilainya dicatat sebagai biaya.',
    read: 'Di Insight F&B = jurnal item ESB dengan purpose WASTE, bernilai HPP. Persen waste = waste dibagi penjualan bersih outlet pada periode yang sama.',
    match: '\\bwaste\\b',
  },
  {
    id: 'pemakaian-internal',
    term: 'Pemakaian internal',
    def: 'Barang yang keluar dari stok outlet untuk dipakai sendiri (perlengkapan produksi, kemasan, kebersihan, seragam), bukan dijual.',
    read: 'Pemetaan purpose jurnal ESB ke kelas ini masih sementara sampai dikonfirmasi Ops/Finance.',
    match: 'pemakaian internal',
  },
  {
    id: 'fill-rate-line',
    term: 'Fill rate outlet (baris)',
    full: 'Fill rate outlet ke CK: rata-rata pemenuhan per baris permintaan',
    def: 'Rata-rata pemenuhan per baris permintaan transfer outlet ke CK: qty yang dikirim dibagi qty yang diminta, dibatasi maksimum 100% per baris.',
    read: 'Kirim lebih tidak menaikkan skor. Hanya permintaan matang (selesai atau berumur 7 hari lebih) dan satuan yang sama dihitung; belum ada target resmi.',
    match: 'fill ?rate outlet',
  },
  {
    id: 'permintaan-penuh',
    term: '% permintaan penuh',
    def: 'Persentase permintaan yang seluruh barisnya terkirim penuh (qty dikirim sama dengan atau lebih dari qty diminta).',
    read: 'Lebih ketat daripada fill rate baris: satu baris kurang kirim membuat seluruh permintaannya tidak penuh.',
    match: '% ?permintaan penuh',
  },
  {
    id: 'waktu-kirim',
    term: 'Waktu kirim',
    def: 'Selisih hari antara tanggal permintaan dan tanggal kirim pertama, dirata-rata untuk baris yang sudah terkirim.',
    read: 'Baris yang belum terkirim sama sekali tidak ikut dirata-rata.',
    match: 'waktu kirim',
  },
  {
    id: 'permintaan-matang',
    term: 'Permintaan matang',
    def: 'Permintaan yang sudah selesai atau berumur lebih dari 7 hari sejak tanggal permintaan, sehingga pengirimannya dianggap final.',
    read: 'Permintaan yang belum matang masih berjalan dan tidak masuk hitungan fill rate.',
    match: '(?:permintaan|baris) matang',
  },
];

const BY_ID = new Map(GLOSSARY.map((e) => [e.id, e]));
const COMPILED = GLOSSARY.map((e) => ({ id: e.id, re: new RegExp(e.match, e.caseSensitive ? '' : 'i') }));

export function getTerm(id: string): GlossaryEntry | undefined {
  return BY_ID.get(id);
}

/** id istilah yang muncul di `text` (urut sesuai GLOSSARY, tanpa duplikat). */
export function termsIn(text: string): string[] {
  if (!text) return [];
  return COMPILED.filter((c) => c.re.test(text)).map((c) => c.id);
}

/** Definisi satu kalimat untuk tooltip native (title). */
export function termSummary(e: GlossaryEntry): string {
  return `${e.term}${e.full ? ` (${e.full})` : ''}: ${e.def}`;
}
