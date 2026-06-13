import axios from 'axios';

/**
 * Bull이 재시도하지 않아야 하는 에러 (4xx 클라이언트 오류 등).
 * Bull v4는 err.unrecoverable === true 이면 남은 attempts 소진 없이 즉시 failed 처리.
 */
export class PermanentJobError extends Error {
  readonly unrecoverable = true;

  constructor(message: string) {
    super(message);
    this.name = 'PermanentJobError';
  }
}

export type ErrorCategory = 'network' | 'rate_limit' | 'server' | 'client' | 'timeout' | 'unknown';

export function classifyError(err: unknown): ErrorCategory {
  if (axios.isAxiosError(err)) {
    if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) return 'timeout';
    const status = err.response?.status;
    if (!status) return 'network'; // ECONNREFUSED, ETIMEDOUT, ECONNRESET
    if (status === 429) return 'rate_limit';
    if (status >= 500) return 'server';
    if (status >= 400) return 'client';
  }
  return 'unknown';
}

/**
 * 에러 유형에 따라 적절한 에러를 throw.
 * - client(4xx): PermanentJobError → Bull 즉시 실패, 재시도 없음
 * - 나머지: 원본 에러 rethrow → Bull이 backoff 설정에 따라 재시도
 */
export function throwForRetryPolicy(err: unknown, context: string): never {
  const category = classifyError(err);
  const msg = err instanceof Error ? err.message : String(err);

  if (category === 'client') {
    throw new PermanentJobError(`[${context}] 클라이언트 오류 (재시도 불가): ${msg}`);
  }

  throw err instanceof Error ? err : new Error(`[${context}] ${msg}`);
}
