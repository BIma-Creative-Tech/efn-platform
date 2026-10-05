import { Inject, Injectable, OnApplicationShutdown } from '@nestjs/common';
import Redis from 'ioredis';
import { Pool } from 'pg';
import { PG_POOL } from './db/db';
import { REDIS_CLIENT } from './redis/kv-store';

/** Menutup koneksi Redis & PostgreSQL saat shutdown (SIGTERM dari Docker). */
@Injectable()
export class InfraLifecycle implements OnApplicationShutdown {
  constructor(
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    @Inject(PG_POOL) private readonly pool: Pool,
  ) {}

  async onApplicationShutdown(): Promise<void> {
    await Promise.allSettled([this.redis.quit(), this.pool.end()]);
  }
}
