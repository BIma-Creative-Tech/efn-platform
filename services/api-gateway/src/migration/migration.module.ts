import { Module } from '@nestjs/common';
import { LoyaltyModule } from '../loyalty/loyalty.module';
import { MigrationKeyGuard } from './migration-key.guard';
import { MigrationController } from './migration.controller';
import { MigrationService } from './migration.service';

/** Migrasi member & saldo pembuka (docs/04 §9.6 K-7). */
@Module({
  imports: [LoyaltyModule],
  controllers: [MigrationController],
  providers: [MigrationService, MigrationKeyGuard],
})
export class MigrationModule {}
