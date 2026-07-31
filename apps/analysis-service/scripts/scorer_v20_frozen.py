"""
ensemble_v2.0 의 `_momentum_score` 동결 사본.

출처: 커밋 `00affe6^` (= v3.9.0 모멘텀 ∩자 교체 직전) 의
`app/engine/scorer.py`. 아래 함수는 **그 시점 원문 그대로**이며,
반사실(counterfactual) 대조군을 재현하기 위한 역사적 기준선이다.

⚠️ 이 파일은 고치지 말 것. 현행 스코어러가 어떻게 바뀌든 여기는 v2.0 을
그대로 유지해야 "같은 기간·같은 종목에서 옛 모델이 뭘 골랐을까" 를 답할 수 있다.
현행 로직 수정은 `app/engine/scorer.py` 에서 한다.

v2.1 과의 유일한 차이는 mom_5d / mom_20d 두 항이다(나머지는 동일):
  v2.0  mom_20d > 0.10 → +10  (과열에 최대 가점 = 고점 추격)
  v2.1  mom_20d > 0.40 → -15  (과열에 감점 = ∩자)
"""


def momentum_score_v20(features: dict) -> tuple[float, float]:
    """
    기술적 지표 기반 모멘텀 전략 (ensemble_v2.0).
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

    mom_5d = t.get("momentum_5d", 0)
    score += 8 if mom_5d > 0.05 else (4 if mom_5d > 0.02 else (-8 if mom_5d < -0.05 else 0))
    data_points += 1

    mom_20d = t.get("momentum_20d", 0)
    score += 10 if mom_20d > 0.10 else (5 if mom_20d > 0.03 else (-10 if mom_20d < -0.10 else 0))
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

    obv = t.get("obv_trend")
    if obv is not None:
        score += 8 if obv > 0.3 else (4 if obv > 0 else (-6 if obv < -0.3 else -2))
        data_points += 1

    quality = min(1.0, data_points / 8)
    return max(0.0, min(100.0, score)), quality
