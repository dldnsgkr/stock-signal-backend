import { Injectable } from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';

@Injectable()
export class UsersService {
  constructor(private prisma: PrismaService) {}

  syncFromGoogle(data: {
    email: string;
    name?: string;
    googleId: string;
    avatarUrl?: string;
  }) {
    return this.prisma.user.upsert({
      where: { googleId: data.googleId },
      update: {
        email: data.email,
        name: data.name ?? null,
        avatarUrl: data.avatarUrl ?? null,
      },
      create: {
        email: data.email,
        name: data.name ?? null,
        googleId: data.googleId,
        avatarUrl: data.avatarUrl ?? null,
      },
    });
  }
}
