import { Controller, Post, Body } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { UsersService } from './users.service';

@ApiTags('users')
@Controller('users')
export class UsersController {
  constructor(private readonly service: UsersService) {}

  @Post('sync')
  @ApiOperation({ summary: 'Google OAuth 유저 동기화 (NextAuth 콜백에서 호출)' })
  sync(
    @Body()
    body: {
      email: string;
      name?: string;
      googleId: string;
      avatarUrl?: string;
    },
  ) {
    return this.service.syncFromGoogle(body);
  }
}
