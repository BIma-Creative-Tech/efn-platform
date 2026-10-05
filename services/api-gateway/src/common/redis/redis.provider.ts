import { Logger, Provider } from '@nestjs/common';
import Redis from 'ioredis';
import { AppConfig } from '../../config/app-config';
import { IoRedisKvStore, KV_STORE, REDIS_CLIENT } from './kv-store';

export const redisProviders: Provider[] = [
  {
    provide: REDIS_CLIENT,
    inject: [AppConfig],
    useFactory: (config: AppConfig): Redis => {
      const logger = new Logger('Redis');
      const c = config.redisConnection;
      // rediss:// + CA internal (docs/04 §7.1) dan user ACL `efn-bff` (SEC-05). URL sudah tanpa kredensial.
      const client = new Redis(c.url, {
        ...(c.username ? { username: c.username } : {}),
        ...(c.password ? { password: c.password } : {}),
        ...(c.tls ? { tls: { ...c.tls } } : {}),
        maxRetriesPerRequest: 2,
        enableReadyCheck: true,
      });
      // Tanpa handler, ioredis mencetak error tak tertangani; pesan tidak memuat kredensial
      client.on('error', (err: Error) => logger.error(`Redis error: ${err.message}`));
      return client;
    },
  },
  {
    provide: KV_STORE,
    inject: [REDIS_CLIENT],
    useFactory: (client: Redis): IoRedisKvStore => new IoRedisKvStore(client),
  },
];
