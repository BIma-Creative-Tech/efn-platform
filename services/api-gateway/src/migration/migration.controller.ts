import { Body, Controller, HttpCode, HttpStatus, Post, UseGuards } from '@nestjs/common';
import { parseMigrationMember, parseOpeningBalance } from './dto/migration.dto';
import { MigrationKeyGuard } from './migration-key.guard';
import { MigrationMemberResponse, MigrationService, OpeningBalanceResponse } from './migration.service';

/**
 * K-7 (docs/04 §9.6): `/internal/v1/migration/*` — di luar prefix global `v1` (lihat `main.ts`), tidak dirutekan
 * Traefik publik (`!PathPrefix(/internal)`), aktif hanya bila `MIGRATION_ENABLED=true` + `X-EFN-Migration-Key`.
 * Identitas hanya di body (R2).
 */
@Controller('internal/v1/migration')
@UseGuards(MigrationKeyGuard)
export class MigrationController {
  constructor(private readonly migration: MigrationService) {}

  @Post('members')
  @HttpCode(HttpStatus.OK)
  members(@Body() body: unknown): Promise<MigrationMemberResponse> {
    return this.migration.importMember(parseMigrationMember(body));
  }

  @Post('opening-balance')
  @HttpCode(HttpStatus.OK)
  openingBalance(@Body() body: unknown): Promise<OpeningBalanceResponse> {
    return this.migration.openingBalance(parseOpeningBalance(body));
  }
}
