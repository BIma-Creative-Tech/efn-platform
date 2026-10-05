import { Logger, Provider } from '@nestjs/common';
import { Pool } from 'pg';
import { AppConfig } from '../../config/app-config';
import { DB, PG_POOL, PgDb } from './db';

/** Dipakai provider Nest dan CLI (`src/cli/*`). */
export function createPgPool(config: AppConfig): Pool {
  const c = config.pgConnection;
  // Field eksplisit (bukan connectionString) agar password dari secret dan opsi `ssl` tidak ditimpa hasil
  // parse URL (pg mengabaikan objek ssl bila connectionString memuat sslmode).
  // TLS verify-full (docs/04 §7.1): CA internal, rejectUnauthorized, servername = host, TLS ≥ 1.2.
  const pool = new Pool({
    host: c.host,
    port: c.port,
    database: c.database,
    user: c.user,
    password: c.password,
    max: c.max,
    ssl: c.ssl === false ? false : { ...c.ssl },
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
    statement_timeout: 10_000,
    application_name: 'efn-api-gateway',
  });
  const logger = new Logger('PgPool');
  pool.on('error', (err: Error) => logger.error(`PostgreSQL idle client error: ${err.message}`));
  return pool;
}

export const pgProviders: Provider[] = [
  {
    provide: PG_POOL,
    inject: [AppConfig],
    useFactory: (config: AppConfig): Pool => createPgPool(config),
  },
  {
    provide: DB,
    inject: [PG_POOL],
    useFactory: (pool: Pool): PgDb => new PgDb(pool),
  },
];
