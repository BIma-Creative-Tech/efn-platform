const rupiah = new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat('id-ID');

export function formatRupiah(value: number): string {
  return rupiah.format(Math.round(value));
}

export function formatNumber(value: number): string {
  return integer.format(value);
}
