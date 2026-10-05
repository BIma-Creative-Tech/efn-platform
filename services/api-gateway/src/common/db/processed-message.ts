import { Queryable } from './db';

/**
 * Klaim idempotency consumer (kontrak §4): INSERT ke `platform.processed_message` dengan PK
 * (consumer, idempotency_key). Panggil di DALAM transaksi yang sama dengan efek bisnisnya:
 * - true  → pesan baru, lanjutkan efek;
 * - false → sudah pernah diproses, lewati (ack).
 * Bila efek gagal, transaksi rollback sehingga klaim ikut batal dan pesan bisa diproses ulang.
 */
export async function claimMessage(q: Queryable, consumer: string, idempotencyKey: string): Promise<boolean> {
  const res = await q.query<{ consumer: string }>(
    `INSERT INTO platform.processed_message (consumer, idempotency_key)
     VALUES ($1, $2)
     ON CONFLICT (consumer, idempotency_key) DO NOTHING
     RETURNING consumer`,
    [consumer, idempotencyKey],
  );
  return (res.rowCount ?? 0) > 0;
}
