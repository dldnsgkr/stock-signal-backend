"""
ensemble_v2.1 의 `_momentum_score` 동결 사본.

출처: 태그 `v3.16.2` (= v3.17.0 과열 감점 배포 직전) 의
`app/engine/scorer.py`. 아래 함수는 **그 시점 원문 그대로**이며,
2026-07-27 ~ 08-25 구간(v2.1 로 채점됨)을 재채점할 때 쓴다.

⚠️ 이 파일은 고치지 말 것 — `scorer_v20_frozen.py` 와 같은 원칙.
현행(v2.2, 2026-08-26~)과의 유일한 차이는 과열 추격 추가 감점이다:
  v2.2  mom_20d >= 0.30 → -25 / >= 0.20 → -12 / >= 0.15 → -5  (클리핑 전)
  v2.1  (이 블록이 없다)
"""


def momentum_score_v21(features: dict) -> tuple[float, float]:
    """
    기술적 지표 기반 모멘텀 전략.
    반환: (score 0~100, data_quality 0~1)
    """
    t = features["technical"]
    score = 50.0
    data_points = 0

    ma20_pos = t.get("ma20_position", 0)
    score += 15 if ma20_pos > 0.05 else (8 if ma20_pos > 0 else (-15 if ma20_pos < -0.05 else -8))
    data_points += 1

    ma60_pos = t.get("ma60_position", 0)
    score += 10 if ma60_pos > 0.03 else (-10 if ma60_pos < -0.03 else 0)
    data_points += 1

    # 단기 반전(short-term reversal, ensemble_v2.1): 완만한 모멘텀에 가점,
    # 과열엔 감점하는 ∩자. 과거엔 mom>10% 에 최대 가점을 줘서 고점을 추격하다
    # 되돌림을 맞았다 — 20일 모멘텀 구간별 7일 알파가 KR/US 모두 ∩자로 확인됨
    # (0~10% 최선, 40%+ 최악). 오프라인 백테스트: US top20 알파 +4.0%p·적중 +8.2%p,
    # KR +1.3%p·+1.9%p 개선. (단기 반전은 검증된 이례현상 — Jegadeesh 1990~)
    mom_5d = t.get("momentum_5d", 0)
    score += (-8 if mom_5d > 0.15 else -2 if mom_5d > 0.08 else 6 if mom_5d > 0.02
              else 3 if mom_5d > 0 else 1 if mom_5d > -0.05 else -4 if mom_5d > -0.15 else -8)
    data_points += 1

    mom_20d = t.get("momentum_20d", 0)
    score += (-15 if mom_20d > 0.40 else -8 if mom_20d > 0.20 else -2 if mom_20d > 0.10
              else 8 if mom_20d > 0.03 else 4 if mom_20d > 0 else 2 if mom_20d > -0.10
              else -4 if mom_20d > -0.20 else -8)
    data_points += 1

    vol_growth = t.get("volume_growth_rate", 0)
    score += 12 if vol_growth > 0.5 else (6 if vol_growth > 0.2 else (-8 if vol_growth < -0.3 else 0))
    data_points += 1

    rsi = t.get("rsi")
    if rsi is not None:
        if rsi < 30:
            score += 15
        elif rsi < 45:
            score += 8
        elif rsi > 70:
            score -= 12
        elif rsi > 60:
            score -= 4
        data_points += 1

    macd_h = t.get("macd_histogram")
    macd_h_prev = t.get("macd_histogram_prev")
    if macd_h is not None:
        score += 8 if macd_h > 0 else -8
        if macd_h_prev is not None:
            if macd_h > 0 and macd_h_prev <= 0:
                score += 7
            elif macd_h < 0 and macd_h_prev >= 0:
                score -= 7
        data_points += 1

    bb_pos = t.get("bb_position")
    if bb_pos is not None:
        if bb_pos < 0:
            score += 12
        elif bb_pos < 0.2:
            score += 6
        elif bb_pos > 1:
            score -= 10
        elif bb_pos > 0.8:
            score -= 4
        data_points += 1

    # OBV 추세
    obv = t.get("obv_trend")
    if obv is not None:
        score += 8 if obv > 0.3 else (4 if obv > 0 else (-6 if obv < -0.3 else -2))
        data_points += 1

    quality = min(1.0, data_points / 8)
    return max(0.0, min(100.0, score)), quality


