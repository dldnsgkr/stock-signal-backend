import { NestFactory } from '@nestjs/core';
import { WorkerAppModule } from './worker.app.module';

// BigInt → Number 직렬화 (API와 동일)
(BigInt.prototype as any).toJSON = function () {
  return Number(this);
};

async function bootstrap() {
  const app = await NestFactory.createApplicationContext(WorkerAppModule);
  app.enableShutdownHooks();
  console.log('Worker process started — listening for Bull queue jobs');
}

bootstrap();
