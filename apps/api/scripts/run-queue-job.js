#!/usr/bin/env node
/**
 * Bull 큐에 잡 하나를 넣고 끝날 때까지 기다린다 — GitHub Actions 용 원샷 투입기.
 * API 서버(스케줄러 크론 포함)를 띄우지 않고 워커만으로 파이프라인을 돌리기 위해 존재한다.
 *
 * 사용법: node scripts/run-queue-job.js <queue> <jobName> '<jsonData>' [timeoutMin=150]
 *   node scripts/run-queue-job.js run-pipeline run '{"market":"KR"}' 140
 *   node scripts/run-queue-job.js evaluate-recommendations evaluate '{}' 50
 */
const Queue = require('bull');

async function main() {
  const [queueName, jobName, dataJson, timeoutMinArg] = process.argv.slice(2);
  if (!queueName || !jobName) {
    console.error('사용법: run-queue-job.js <queue> <jobName> [jsonData] [timeoutMin]');
    process.exit(2);
  }
  const data = JSON.parse(dataJson || '{}');
  const deadline = Date.now() + (Number(timeoutMinArg) || 150) * 60000;

  const queue = new Queue(queueName, process.env.REDIS_URL || 'redis://localhost:6379');
  const job = await queue.add(jobName, data, {
    attempts: 1,
    removeOnComplete: false,
    removeOnFail: false,
  });
  console.log(`[run-queue-job] ${queueName}/${jobName} job#${job.id} 투입 data=${JSON.stringify(data)}`);

  let lastLog = 0;
  for (;;) {
    const state = await job.getState();
    if (state === 'completed') {
      console.log(`[run-queue-job] job#${job.id} 완료`);
      await queue.close();
      process.exit(0);
    }
    if (state === 'failed') {
      const fresh = await queue.getJob(job.id);
      console.error(`[run-queue-job] job#${job.id} 실패: ${fresh && fresh.failedReason}`);
      await queue.close();
      process.exit(1);
    }
    if (Date.now() > deadline) {
      console.error(`[run-queue-job] job#${job.id} 제한시간 초과 (state=${state})`);
      await queue.close();
      process.exit(1);
    }
    if (Date.now() - lastLog > 60000) {
      console.log(`[run-queue-job] 대기 중 state=${state} progress=${job.progress()}`);
      lastLog = Date.now();
    }
    await new Promise((r) => setTimeout(r, 15000));
  }
}

main().catch((e) => {
  console.error('[run-queue-job] 오류:', e.message);
  process.exit(1);
});
