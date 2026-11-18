/* Terapkan tema tersimpan sebelum cat pertama (hindari kilatan). 'system' = tanpa atribut (ikut prefers-color-scheme). */
(function () {
  try {
    var t = localStorage.getItem('insight-theme');
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch {
    /* localStorage tidak tersedia: ikuti sistem */
  }
})();
