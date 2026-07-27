import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class NotificationsService {
  constructor(private readonly prisma: PrismaService) {}

  // 유저의 알림 이력: 본인 타겟(userId) + 시장 전체(broadcast, user_id NULL)
  async listForUser(userId: number, limit = 50) {
    if (!userId) return [];
    const rows = await this.prisma.notificationLog.findMany({
      where: { OR: [{ userId }, { userId: null }] },
      orderBy: { createdAt: 'desc' },
      take: Math.min(limit, 100),
    });
    return rows.map(r => ({
      id: r.id,
      kind: r.kind,
      title: r.title,
      body: r.body,
      url: r.url,
      market: r.market,
      scope: r.userId ? 'personal' : 'broadcast',
      createdAt: r.createdAt,
    }));
  }
}
