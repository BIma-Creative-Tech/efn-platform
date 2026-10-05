/**
 * Topologi RabbitMQ sesuai docs/04_SERVICE_CONTRACTS.md §4:
 * default exchange, queue durable tipe quorum, DLQ `<queue>.dlq` via
 * `x-dead-letter-exchange: ""` + `x-dead-letter-routing-key`.
 *
 * PENTING: argumen harus identik di semua service yang men-declare queue yang sama (BFF, ai-ocr-worker),
 * jika tidak broker menolak dengan PRECONDITION_FAILED (406).
 */
export const QUEUES = {
  OCR_REQUESTED: 'ocr.requested',
  LOYALTY_EARN_REQUESTED: 'loyalty.earn.requested',
  NOTIFY_PUSH: 'notify.push',
  /** docs/04 §8.4: publisher efn-etl (store clickhouse), consumer BFF. */
  PRIVACY_ERASURE_COMPLETED: 'privacy.erasure.completed',
} as const;

export type QueueName = (typeof QUEUES)[keyof typeof QUEUES];

export const ALL_QUEUES: readonly QueueName[] = [
  QUEUES.OCR_REQUESTED,
  QUEUES.LOYALTY_EARN_REQUESTED,
  QUEUES.NOTIFY_PUSH,
  QUEUES.PRIVACY_ERASURE_COMPLETED,
];

export function dlqName(queue: string): string {
  return `${queue}.dlq`;
}

export function mainQueueArguments(queue: string): Record<string, string> {
  return {
    'x-queue-type': 'quorum',
    'x-dead-letter-exchange': '',
    'x-dead-letter-routing-key': dlqName(queue),
  };
}

export function dlqArguments(): Record<string, string> {
  return { 'x-queue-type': 'quorum' };
}

/** Queue belum dideklarasikan broker (definitions) — kesalahan konfigurasi deploy, bukan gangguan sementara. */
export class QueueMissingError extends Error {}

export interface QueueChecker {
  checkQueue(queue: string): Promise<unknown>;
}

/**
 * docs/04 §8.10 (SEC-25): queue & DLQ dideklarasikan BROKER (definitions DevSecOps); BFF hanya passive declare
 * (`checkQueue`) sehingga user `efn-bff` tidak butuh hak `configure`. Queue hilang → `QueueMissingError` berpesan jelas.
 */
export async function checkQueueWithDlq(ch: QueueChecker, queue: string): Promise<void> {
  for (const q of [dlqName(queue), queue]) {
    try {
      await ch.checkQueue(q);
    } catch (err) {
      const code = (err as { code?: unknown } | null)?.code;
      throw new QueueMissingError(
        `Queue RabbitMQ '${q}' belum dideklarasikan broker (definitions) atau tidak dapat diakses` +
          `${code !== undefined ? ` (kode ${String(code)})` : ''} — BFF memakai passive declare (docs/04 §8.10)`,
      );
    }
  }
}

/** @deprecated Deklarasi aktif (sebelum §8.10). Dipertahankan untuk spec argumen kontrak; tidak dipakai runtime. */
export interface QueueAsserter {
  assertQueue(queue: string, options: { durable: boolean; arguments: Record<string, string> }): Promise<unknown>;
}

/** Declare DLQ dulu, lalu queue utama (urutan tidak wajib, tetapi DLQ harus ada sebelum pesan mati). */
export async function assertQueueWithDlq(ch: QueueAsserter, queue: string): Promise<void> {
  await ch.assertQueue(dlqName(queue), { durable: true, arguments: dlqArguments() });
  await ch.assertQueue(queue, { durable: true, arguments: mainQueueArguments(queue) });
}
