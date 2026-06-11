import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bull';
import { RecommendationsController } from './recommendations.controller';
import { RecommendationsService } from './recommendations.service';

@Module({
  imports: [
    BullModule.registerQueue({ name: 'recommendations' }),
  ],
  controllers: [RecommendationsController],
  providers: [RecommendationsService],
  exports: [RecommendationsService],
})
export class RecommendationsModule {}
