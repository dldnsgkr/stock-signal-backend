import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import * as webpush from 'web-push';
import { PrismaService } from '../../prisma/prisma.service';

export interface PushPayload {
  title: string;
  body: string;
  url?: string; // 클릭 시 이동할 경로 (프론트 기준 상대 경로)
  tag?: string; // 같은 tag 는 최신 알림으로 교체됨
}

@Injectable()
export class PushService {
  private readonly logger = new Logger(PushService.name);
  private enabled = false;

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
  ) {
    const publicKey = this.config.get<string>('VAPID_PUBLIC_KEY');
    const privateKey = this.config.get<string>('VAPID_PRIVATE_KEY');
    const subject = this.config.get<string>('VAPID_SUBJECT', 'mailto:admin@stock-signal.local');

    if (publicKey && privateKey) {
      webpush.setVapidDetails(subject, publicKey, privateKey);
      this.enabled = true;
    } else {
      this.logger.warn('VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY not set — web push disabled');
    }
  }

  getPublicKey(): string | null {
    return this.enabled ? (this.config.get<string>('VAPID_PUBLIC_KEY') ?? null) : null;
  }

  async subscribe(endpoint: string, p256dh: string, auth: string) {
    await this.prisma.pushSubscription.upsert({
      where: { endpoint },
      create: { endpoint, p256dh, auth },
      update: { p256dh, auth },
    });
    return { subscribed: true };
  }

  async unsubscribe(endpoint: string) {
    await this.prisma.pushSubscription.deleteMany({ where: { endpoint } });
    return { subscribed: false };
  }

  /** 모든 구독 브라우저에 발송. 실패해도 throw 하지 않는다(호출부 job 에 영향 금지). */
  async sendToAll(payload: PushPayload): Promise<{ sent: number; removed: number }> {
    if (!this.enabled) return { sent: 0, removed: 0 };

    const subs = await this.prisma.pushSubscription.findMany();
    if (subs.length === 0) return { sent: 0, removed: 0 };

    const body = JSON.stringify(payload);
    let sent = 0;
    let removed = 0;

    for (const sub of subs) {
      try {
        await webpush.sendNotification(
          { endpoint: sub.endpoint, keys: { p256dh: sub.p256dh, auth: sub.auth } },
          body,
        );
        sent++;
      } catch (err: any) {
        // 404/410 = 구독 만료(브라우저에서 해지) → 정리
        if (err?.statusCode === 404 || err?.statusCode === 410) {
          await this.prisma.pushSubscription.delete({ where: { id: sub.id } }).catch(() => {});
          removed++;
        } else {
          this.logger.error(`Push send failed (${sub.id}): ${err?.message ?? err}`);
        }
      }
    }

    this.logger.log(`Push "${payload.title}": sent=${sent}, removed=${removed}, total=${subs.length}`);
    return { sent, removed };
  }
}
