import { Injectable, OnModuleInit, Logger } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bull';
import { Queue, Job } from 'bull';
import { AlertService } from '../alert/alert.service';

const MONITORED: { queue: string; label: string }[] = [
  { queue: 'run-pipeline',             label: '전체 파이프라인' },
  { queue: 'generate-recommendations', label: '시그널 생성' },
  { queue: 'collect-prices',           label: '주가 수집' },
  { queue: 'collect-news',             label: '뉴스 수집' },
  { queue: 'collect-financials',       label: '재무 수집' },
];

@Injectable()
export class QueueMonitorService implements OnModuleInit {
  private readonly logger = new Logger(QueueMonitorService.name);

  constructor(
    @InjectQueue('run-pipeline')             private pipelineQueue: Queue,
    @InjectQueue('generate-recommendations') private recsQueue: Queue,
    @InjectQueue('collect-prices')           private pricesQueue: Queue,
    @InjectQueue('collect-news')             private newsQueue: Queue,
    @InjectQueue('collect-financials')       private financialsQueue: Queue,
    private readonly alert: AlertService,
  ) {}

  onModuleInit() {
    const pairs: [Queue, string][] = [
      [this.pipelineQueue, '전체 파이프라인'],
      [this.recsQueue,     '시그널 생성'],
      [this.pricesQueue,   '주가 수집'],
      [this.newsQueue,     '뉴스 수집'],
      [this.financialsQueue, '재무 수집'],
    ];

    for (const [queue, label] of pairs) {
      queue.on('failed', (job: Job, err: Error) => {
        const market = job.data?.market ?? '-';
        this.logger.error(`[${label}] job#${job.id} failed: ${err.message}`);
        this.alert.send({
          type: 'error',
          title: `${label} 실패`,
          market,
          message: err.message.slice(0, 200),
          detail: `Job ID: ${job.id} | 시도: ${job.attemptsMade}회`,
        });
      });

      // 파이프라인 완료는 성공 알림도 발송
      if (label === '전체 파이프라인') {
        queue.on('completed', (job: Job) => {
          const market = job.data?.market ?? '-';
          this.alert.send({
            type: 'success',
            title: '파이프라인 완료',
            market,
          });
        });
      }
    }

    this.logger.log('Queue failure monitors attached');
  }
}
