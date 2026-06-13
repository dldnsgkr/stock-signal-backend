import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import * as nodemailer from 'nodemailer';

export interface BuySignalPayload {
  symbol: string;
  name: string;
  market: string;
  score: number;
  confidence: number;
  entryPrice: number;
  reasons: string[];
}

@Injectable()
export class EmailService {
  private readonly logger = new Logger(EmailService.name);
  private readonly transporter: nodemailer.Transporter | null = null;
  private readonly from: string;

  constructor(private readonly config: ConfigService) {
    const host = config.get<string>('SMTP_HOST');
    const user = config.get<string>('SMTP_USER');
    const pass = config.get<string>('SMTP_PASS');
    this.from = config.get<string>('SMTP_FROM') ?? user ?? 'noreply@stock-signal.local';

    if (host && user && pass) {
      this.transporter = nodemailer.createTransport({
        host,
        port: config.get<number>('SMTP_PORT') ?? 587,
        secure: false,
        auth: { user, pass },
      });
      this.logger.log(`Email service ready (${host})`);
    } else {
      this.logger.warn('SMTP_HOST/USER/PASS not set — emails will only be logged');
    }
  }

  async sendBuySignalAlert(to: string, payload: BuySignalPayload): Promise<void> {
    const flagEmoji = payload.market === 'KR' ? '🇰🇷' : '🇺🇸';
    const scoreBar = '█'.repeat(Math.round(payload.score / 10)) + '░'.repeat(10 - Math.round(payload.score / 10));
    const reasonsHtml = payload.reasons.map(r => `<li>${r}</li>`).join('');

    const html = `
<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:560px;margin:32px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
    <div style="background:linear-gradient(135deg,#1e40af,#3b82f6);padding:28px 32px;color:#fff">
      <div style="font-size:13px;opacity:.8;margin-bottom:6px">${flagEmoji} ${payload.market} 시장 · 매수 시그널</div>
      <div style="font-size:26px;font-weight:700;letter-spacing:-.5px">${payload.symbol}</div>
      <div style="font-size:15px;opacity:.85;margin-top:2px">${payload.name}</div>
    </div>
    <div style="padding:28px 32px;border-bottom:1px solid #f1f5f9">
      <div style="display:flex;gap:20px;flex-wrap:wrap">
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">스코어</div>
          <div style="font-size:28px;font-weight:700;color:#1e40af">${payload.score.toFixed(1)}</div>
          <div style="font-size:11px;color:#94a3b8;font-family:monospace">${scoreBar}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">신뢰도</div>
          <div style="font-size:28px;font-weight:700;color:#059669">${payload.confidence}%</div>
        </div>
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">진입가</div>
          <div style="font-size:22px;font-weight:600;color:#0f172a">$${payload.entryPrice.toFixed(2)}</div>
        </div>
      </div>
    </div>
    ${payload.reasons.length > 0 ? `
    <div style="padding:24px 32px;border-bottom:1px solid #f1f5f9">
      <div style="font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">매수 근거</div>
      <ul style="margin:0;padding:0 0 0 18px;color:#334155;line-height:1.8;font-size:14px">
        ${reasonsHtml}
      </ul>
    </div>` : ''}
    <div style="padding:20px 32px;background:#f8fafc">
      <div style="font-size:12px;color:#94a3b8;line-height:1.7">
        이 알림은 <strong>${to}</strong>가 <strong>${payload.symbol}</strong> 종목의 BUY 시그널 알림을 구독하여 발송되었습니다.<br>
        ⚠️ 본 시그널은 참고용이며, 투자는 본인의 판단과 책임 하에 이루어져야 합니다.
      </div>
    </div>
  </div>
</body>
</html>`;

    const subject = `[Stock Signal] ${flagEmoji} ${payload.symbol} 매수 시그널 — 스코어 ${payload.score.toFixed(1)}`;

    this.logger.log(`Sending BUY signal email to ${to} for ${payload.symbol} (score=${payload.score.toFixed(1)})`);

    if (!this.transporter) return;

    try {
      await this.transporter.sendMail({ from: this.from, to, subject, html });
    } catch (err) {
      this.logger.error(`Failed to send email to ${to}: ${err}`);
    }
  }
}
