import type { Pool, PoolClient } from 'pg';

export const DB = Symbol('DB');
export const PG_POOL = Symbol('PG_POOL');

export interface QueryResultLike<R> {
  rows: R[];
  rowCount: number | null;
}

/** Hanya query parametris ($1, $2, ...). Jangan pernah merangkai input pengguna ke teks SQL. */
export interface Queryable {
  query<R = Record<string, unknown>>(text: string, params?: readonly unknown[]): Promise<QueryResultLike<R>>;
}

export interface Db extends Queryable {
  /** Menjalankan `fn` dalam satu transaksi (BEGIN/COMMIT, ROLLBACK bila melempar). */
  tx<T>(fn: (q: Queryable) => Promise<T>): Promise<T>;
}

class ClientQueryable implements Queryable {
  constructor(private readonly client: PoolClient) {}

  async query<R = Record<string, unknown>>(text: string, params?: readonly unknown[]): Promise<QueryResultLike<R>> {
    const res = await this.client.query(text, params ? [...params] : undefined);
    return { rows: res.rows as R[], rowCount: res.rowCount };
  }
}

export class PgDb implements Db {
  constructor(private readonly pool: Pool) {}

  async query<R = Record<string, unknown>>(text: string, params?: readonly unknown[]): Promise<QueryResultLike<R>> {
    const res = await this.pool.query(text, params ? [...params] : undefined);
    return { rows: res.rows as R[], rowCount: res.rowCount };
  }

  async tx<T>(fn: (q: Queryable) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      const result = await fn(new ClientQueryable(client));
      await client.query('COMMIT');
      return result;
    } catch (err) {
      await client.query('ROLLBACK').catch(() => undefined);
      throw err;
    } finally {
      client.release();
    }
  }
}
