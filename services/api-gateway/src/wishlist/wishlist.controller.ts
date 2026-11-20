import { Body, Controller, Delete, Get, Header, HttpCode, HttpStatus, Param, Post, Put, Query, UseGuards } from '@nestjs/common';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { parseAlertId, parseChannelCode, parsePriceAlert, parseProductId, parseRestockAlert, parseWishlistItem } from './dto/wishlist.dto';
import { PriceAlertView, RestockAlertView, WishlistService, WishlistView } from './wishlist.service';

/** Wishlist, notify-me, price alert (docs/04 §28.8), Bearer member, `Cache-Control: no-store`. */
@Controller('me')
@UseGuards(JwtAuthGuard)
export class WishlistController {
  constructor(private readonly wishlist: WishlistService) {}

  @Get('wishlist')
  @Header('Cache-Control', 'no-store')
  list(@CurrentUser() user: AuthUser, @Query('channel') channel: unknown): Promise<WishlistView> {
    return this.wishlist.list(user, channel === undefined || channel === '' ? null : parseChannelCode(channel));
  }

  @Put('wishlist/items')
  @HttpCode(HttpStatus.NO_CONTENT)
  @Header('Cache-Control', 'no-store')
  async put(@CurrentUser() user: AuthUser, @Body() body: unknown): Promise<void> {
    await this.wishlist.put(user, parseWishlistItem(body));
  }

  @Delete('wishlist/items/:channel_code/:product_id')
  @HttpCode(HttpStatus.NO_CONTENT)
  @Header('Cache-Control', 'no-store')
  async remove(@CurrentUser() user: AuthUser, @Param('channel_code') channel: string, @Param('product_id') productId: string): Promise<void> {
    await this.wishlist.remove(user, parseChannelCode(channel), parseProductId(productId));
  }

  @Get('restock-alerts')
  @Header('Cache-Control', 'no-store')
  listRestock(@CurrentUser() user: AuthUser): Promise<{ items: RestockAlertView[] }> {
    return this.wishlist.listRestock(user);
  }

  @Post('restock-alerts')
  @HttpCode(HttpStatus.CREATED)
  @Header('Cache-Control', 'no-store')
  createRestock(@CurrentUser() user: AuthUser, @Body() body: unknown): Promise<{ id: string }> {
    return this.wishlist.createRestock(user, parseRestockAlert(body));
  }

  @Delete('restock-alerts/:id')
  @HttpCode(HttpStatus.NO_CONTENT)
  @Header('Cache-Control', 'no-store')
  async deleteRestock(@CurrentUser() user: AuthUser, @Param('id') id: string): Promise<void> {
    await this.wishlist.deleteRestock(user, parseAlertId(id));
  }

  @Get('price-alerts')
  @Header('Cache-Control', 'no-store')
  listPrice(@CurrentUser() user: AuthUser): Promise<{ items: PriceAlertView[] }> {
    return this.wishlist.listPrice(user);
  }

  @Post('price-alerts')
  @HttpCode(HttpStatus.CREATED)
  @Header('Cache-Control', 'no-store')
  createPrice(@CurrentUser() user: AuthUser, @Body() body: unknown): Promise<{ id: string }> {
    return this.wishlist.createPrice(user, parsePriceAlert(body));
  }

  @Delete('price-alerts/:id')
  @HttpCode(HttpStatus.NO_CONTENT)
  @Header('Cache-Control', 'no-store')
  async deletePrice(@CurrentUser() user: AuthUser, @Param('id') id: string): Promise<void> {
    await this.wishlist.deletePrice(user, parseAlertId(id));
  }
}
