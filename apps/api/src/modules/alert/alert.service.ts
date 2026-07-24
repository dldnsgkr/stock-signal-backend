import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PushService } from './push.service';

interface AlertPayload {
  type: 'error' | 'warning' | 'success';
  title: string;
  message?: string;
  market?: string;
  detail?: string;
}

const EMOJI = { error: ':red_circle:', warning: ':warning:', success: ':white_check_mark:' };

@Injectable()
export class AlertService {
  private readonly logger = new Logger(AlertService.name);
  private readonly webhookUrl: string | undefined;

  constructor(
    private readonly config: ConfigService,
    private readonly push: PushService,
  ) {
    this.webhookUrl = this.config.get<string>('SLACK_WEBHOOK_URL');
    if (!this.webhookUrl) {
      this.logger.warn('SLACK_WEBHOOK_URL not set — alerts will only be logged');
    }
  }

  async send(payload: AlertPayload): Promise<void> {
    const now = new Date().toLocaleString('ko-KR', {
      timeZone: 'Asia/Seoul',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });

    const emoji = EMOJI[payload.type];
    const marketStr = payload.market ? ` [${payload.market}]` : '';
    const lines = [
      `${emoji} *${payload.title}*${marketStr}`,
      payload.message ? `오류: ${payload.message}` : null,
      payload.detail ? payload.detail : null,
      `시각: ${now} KST`,
    ].filter(Boolean).join('\n');

    this.logger.log(`[Alert] ${lines.replace(/\n/g, ' | ')}`);

    // 브라우저 푸시 — Slack/이메일 미설정이어도 알림이 밖으로 나가는 출구
    const prefix = payload.type === 'error' ? '🔴 ' : payload.type === 'warning' ? '⚠️ ' : '✅ ';
    this.push.sendToAll({
      title: `${prefix}${payload.title}${marketStr}`,
      body: [payload.message, payload.detail].filter(Boolean).join('\n') || '상세는 관리자 페이지 참조',
      url: '/admin',
      tag: `alert-${payload.type}`,
    }).catch(e => this.logger.error(`Push alert failed: ${e}`));

    if (!this.webhookUrl) return;

    try {
      const res = await fetch(this.webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: lines }),
      });
      if (!res.ok) {
        this.logger.warn(`Slack webhook returned ${res.status}`);
      }
    } catch (err) {
      this.logger.error(`Failed to send Slack alert: ${err}`);
    }
  }
}
