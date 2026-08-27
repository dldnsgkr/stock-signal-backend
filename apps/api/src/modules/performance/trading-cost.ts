// 거래비용 모델 — "비용 후 알파" 계산의 단일 출처.
//
// 여기 있는 모든 수치는 **가정**이고, 화면 각주에 그대로 노출된다(assumptions()).
// 수치를 바꾸면 화면 설명도 자동으로 따라가지만, 근거 주석은 직접 갱신할 것.
//
// 명시 비용 (왕복):
//   KR — 수수료 0.015% × 2 + 매도 증권거래세·농특세 0.15% (2026년 요율:
//        코스피 거래세 0% + 농특세 0.15%, 코스닥 0.15%) = 0.18%
//   US — 주요 브로커 커미션 0. SEC/TAF 수수료는 bp 미만이라 무시 = 0%
//
// 슬리피지 (편도, 진입일 거래대금 절대 구간별):
//   상대 분위가 아니라 절대 구간을 쓴다 — "이 비용에 체결되는가" 는 시장 내
//   상대 순위가 아니라 절대 유동성의 문제다. 소액(1포지션 수백만 원 수준) 시장가
//   집행을 가정한 보수적 추정치이며, 대량 집행이면 이보다 훨씬 크다.

type SlippageTier = { minTurnover: number; perSideBp: number };

// 내림차순 정렬 유지 — 첫 매칭 구간을 쓴다.
const SLIPPAGE_TIERS: Record<string, SlippageTier[]> = {
  // KRW 기준 일 거래대금
  KR: [
    { minTurnover: 1e10, perSideBp: 5 },   // ≥ 100억
    { minTurnover: 1e9, perSideBp: 15 },   // 10억 ~ 100억
    { minTurnover: 1e8, perSideBp: 40 },   // 1억 ~ 10억
    { minTurnover: 0, perSideBp: 100 },    // < 1억
  ],
  // USD 기준 일 거래대금
  US: [
    { minTurnover: 1e7, perSideBp: 5 },    // ≥ $10M
    { minTurnover: 1e6, perSideBp: 15 },   // $1M ~ $10M
    { minTurnover: 1e5, perSideBp: 40 },   // $0.1M ~ $1M
    { minTurnover: 0, perSideBp: 100 },    // < $0.1M
  ],
};

// 왕복 명시 비용 (소수, 0.0018 = 0.18%)
const EXPLICIT_ROUND_TRIP: Record<string, number> = {
  KR: 0.0018,
  US: 0,
};

/**
 * 왕복 거래비용(소수). 수익률·알파에서 그대로 뺀다.
 * turnover 가 null(진입일 가격 행 없음)이면 **가장 보수적인 구간**을 쓴다 —
 * 유동성을 모르는 종목을 싸게 가정하는 것이 자기기만이기 때문.
 */
export function roundTripCostPct(market: string, turnover: number | null): number {
  const tiers = SLIPPAGE_TIERS[market] ?? SLIPPAGE_TIERS.US;
  const explicit = EXPLICIT_ROUND_TRIP[market] ?? 0;
  const perSideBp =
    turnover == null
      ? tiers[tiers.length - 1].perSideBp
      : (tiers.find((t) => turnover >= t.minTurnover) ?? tiers[tiers.length - 1]).perSideBp;
  return explicit + (perSideBp * 2) / 10000;
}

/** 화면 각주용 가정 설명. 프론트가 이 값을 그대로 렌더링한다. */
export function costAssumptions(market: string) {
  const tiers = SLIPPAGE_TIERS[market] ?? SLIPPAGE_TIERS.US;
  return {
    explicitRoundTripPct: EXPLICIT_ROUND_TRIP[market] ?? 0,
    explicitLabel:
      market === 'KR'
        ? '수수료 0.015%×2 + 매도 거래세 0.15% = 왕복 0.18%'
        : '커미션 0 (미국 주요 브로커 기준)',
    slippageTiers: tiers.map((t) => ({
      minTurnover: t.minTurnover,
      perSideBp: t.perSideBp,
    })),
    note:
      '슬리피지는 진입일 거래대금 구간별 보수적 추정치(소액 시장가 기준)이며 실측이 아닙니다. ' +
      '거래대금을 알 수 없는 종목은 최저 유동성 구간으로 간주합니다.',
  };
}
