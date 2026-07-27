import { Injectable, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

export interface UserSettingsDto {
  defaultMarket: string;
  alertOnBuy: boolean;
  alertOnSell: boolean;
  minAlertScore: number | null;
}

const DEFAULTS: UserSettingsDto = {
  defaultMarket: 'US',
  alertOnBuy: true,
  alertOnSell: true,
  minAlertScore: null,
};

@Injectable()
export class UserSettingsService {
  constructor(private readonly prisma: PrismaService) {}

  // 설정이 없으면 기본값을 반환 (레코드 생성은 저장 시점에)
  async get(userId: number): Promise<UserSettingsDto> {
    if (!userId) throw new BadRequestException('userId required');
    const s = await this.prisma.userSettings.findUnique({ where: { userId } });
    if (!s) return { ...DEFAULTS };
    return {
      defaultMarket: s.defaultMarket,
      alertOnBuy: s.alertOnBuy,
      alertOnSell: s.alertOnSell,
      minAlertScore: s.minAlertScore,
    };
  }

  async update(userId: number, patch: Partial<UserSettingsDto>): Promise<UserSettingsDto> {
    if (!userId) throw new BadRequestException('userId required');

    const data: Record<string, unknown> = {};
    if (patch.defaultMarket !== undefined) {
      const m = String(patch.defaultMarket).toUpperCase();
      if (!['US', 'KR'].includes(m)) throw new BadRequestException('defaultMarket must be US or KR');
      data.defaultMarket = m;
    }
    if (patch.alertOnBuy !== undefined) data.alertOnBuy = !!patch.alertOnBuy;
    if (patch.alertOnSell !== undefined) data.alertOnSell = !!patch.alertOnSell;
    if (patch.minAlertScore !== undefined) {
      const v = patch.minAlertScore;
      if (v === null) data.minAlertScore = null;
      else {
        const n = Number(v);
        if (Number.isNaN(n) || n < 0 || n > 100) throw new BadRequestException('minAlertScore must be 0-100 or null');
        data.minAlertScore = Math.round(n);
      }
    }

    const s = await this.prisma.userSettings.upsert({
      where: { userId },
      create: { userId, ...data },
      update: data,
    });
    return {
      defaultMarket: s.defaultMarket,
      alertOnBuy: s.alertOnBuy,
      alertOnSell: s.alertOnSell,
      minAlertScore: s.minAlertScore,
    };
  }
}
