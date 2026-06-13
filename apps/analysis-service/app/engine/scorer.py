"""
앙상블 시그널 스코어러

3개 독립 전략을 계산한 후 가중 합산.
  - Momentum  : 기술적 지표 중심 (추세·모멘텀·거래량)
  - Value     : 펀더멘털 중심 (ROE·PER·PBR)
  - Sentiment : 뉴스 감성 + 거시지표

전략 간 일치도(agreement)로 confidence를 보정.
데이터 부재 시 해당 전략 비중을 자동으로 재분배.
"""

import numpy as np

BUY_THRESHOLD = 65
WATCH_THRESHOLD = 45

# 기본 전략 가중치
BASE_WEIGHTS = {
    "momentum":  0.45,
    "value":     0.25,
    "sentiment": 0.30,
}


# ── 전략 1: Momentum ──────────────────────────────────────────────────────
def _momentum_score(features: dict) -> tuple[float, float]:
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

    # OBV 추세
    obv = t.get("obv_trend")
    if obv is not None:
        score += 8 if obv > 0.3 else (4 if obv > 0 else (-6 if obv < -0.3 else -2))
        data_points += 1

    quality = min(1.0, data_points / 8)
    return max(0.0, min(100.0, score)), quality


# ── 전략 2: Value ─────────────────────────────────────────────────────────
def _value_score(features: dict) -> tuple[float, float]:
    """
    펀더멘털 기반 가치 전략.
    반환: (score 0~100, data_quality 0~1)
    """
    f = features["fundamental"]
    score = 50.0
    data_points = 0

    roe = f.get("roe")
    if roe is not None:
        if roe > 0.20:
            score += 20
        elif roe > 0.10:
            score += 10
        elif roe > 0:
            score += 3
        elif roe < 0:
            score -= 20
        data_points += 1

    per = f.get("per_relative")
    if per is not None:
        if 0 < per < 10:
            score += 18
        elif 0 < per < 15:
            score += 12
        elif 15 <= per < 25:
            score += 5
        elif 25 <= per < 40:
            score -= 3
        elif per >= 40:
            score -= 12
        data_points += 1

    pbr = f.get("pbr_relative")
    if pbr is not None:
        if 0 < pbr < 1.0:
            score += 15
        elif 0 < pbr < 1.5:
            score += 8
        elif pbr > 5:
            score -= 8
        data_points += 1

    quality = min(1.0, data_points / 3)
    return max(0.0, min(100.0, score)), quality


# ── 전략 3: Sentiment ────────────────────────────────────────────────────
def _sentiment_score(features: dict) -> tuple[float, float]:
    """
    뉴스 감성 + 거시지표 기반 센티멘트 전략.
    반환: (score 0~100, data_quality 0~1)
    """
    n = features["news"]
    m = features["macro"]
    score = 50.0
    data_points = 0

    # 가중 감성 점수 (recency + relevance 반영)
    sentiment_w = n.get("sentiment_weighted", n.get("sentiment_avg", 0))
    if sentiment_w != 0:
        if sentiment_w > 0.4:
            score += 22
        elif sentiment_w > 0.2:
            score += 14
        elif sentiment_w > 0.05:
            score += 7
        elif sentiment_w < -0.4:
            score -= 22
        elif sentiment_w < -0.2:
            score -= 14
        elif sentiment_w < -0.05:
            score -= 7
        data_points += 1

    # 감성 모멘텀 (트렌드 방향)
    momentum = n.get("sentiment_momentum", 0)
    if momentum != 0:
        score += 8 if momentum > 0.15 else (4 if momentum > 0.05 else (-8 if momentum < -0.15 else -4 if momentum < -0.05 else 0))
        data_points += 1

    # 긍/부정 뉴스 비율
    pos = n.get("positive_count", 0)
    neg = n.get("negative_count", 0)
    if pos + neg > 0:
        if pos > neg * 2:
            score += 8
        elif neg > pos * 2:
            score -= 12
        data_points += 1

    # 뉴스 양 자체
    news_count = n.get("news_count", 0)
    if news_count > 0:
        if n.get("news_frequency_spike") and sentiment_w > 0:
            score += 5
        elif n.get("news_frequency_spike") and sentiment_w < 0:
            score -= 5
        data_points += 0.5

    # 거시지표
    vix = m.get("vix")
    if vix is not None:
        if vix > 35:
            score -= 25
        elif vix > 25:
            score -= 15
        elif vix > 20:
            score -= 5
        elif vix < 15:
            score += 10
        data_points += 1

    quality = min(1.0, data_points / 4)
    return max(0.0, min(100.0, score)), quality


# ── 앙상블 합산 ────────────────────────────────────────────────────────────
def calculate_total_score(features: dict) -> dict:
    mom_score, mom_q = _momentum_score(features)
    val_score, val_q = _value_score(features)
    sent_score, sent_q = _sentiment_score(features)

    # 데이터 품질 기반 가중치 재분배
    raw_w = {
        "momentum":  BASE_WEIGHTS["momentum"]  * mom_q,
        "value":     BASE_WEIGHTS["value"]     * val_q,
        "sentiment": BASE_WEIGHTS["sentiment"] * sent_q,
    }
    total_w = sum(raw_w.values())
    if total_w == 0:
        weights = BASE_WEIGHTS
    else:
        weights = {k: v / total_w for k, v in raw_w.items()}

    total = (
        mom_score  * weights["momentum"] +
        val_score  * weights["value"] +
        sent_score * weights["sentiment"]
    )

    return {
        "momentum_score":  round(mom_score, 2),
        "value_score":     round(val_score, 2),
        "sentiment_score": round(sent_score, 2),
        "total_score":     round(total, 2),
        # 하위 호환: 기존 필드명 유지
        "technical_score":   round(mom_score, 2),
        "fundamental_score": round(val_score, 2),
        "news_score":        round(sent_score, 2),
        "macro_score":       round(_sentiment_score(features)[0], 2),
        "flow_score":        round(features["technical"].get("volume_growth_rate", 0) * 10 + 50, 2),
        "_weights": weights,
        "_quality": {"momentum": mom_q, "value": val_q, "sentiment": sent_q},
    }


def determine_action(total_score: float) -> str:
    if total_score >= BUY_THRESHOLD:
        return "BUY"
    elif total_score >= WATCH_THRESHOLD:
        return "WATCH"
    else:
        return "AVOID"


def calculate_confidence(total_score: float, score_detail: dict) -> int:
    """
    전략 간 일치도로 confidence 계산.
    3개 전략이 모두 같은 방향을 가리키면 높은 신뢰도.
    """
    mom = score_detail.get("momentum_score", 50)
    val = score_detail.get("value_score", 50)
    sent = score_detail.get("sentiment_score", 50)

    # 방향 일치 여부
    threshold = 55
    low_threshold = 45
    bullish = [s > threshold for s in [mom, val, sent]]
    bearish = [s < low_threshold for s in [mom, val, sent]]

    agreement_count = sum(bullish) if sum(bullish) > sum(bearish) else sum(bearish)
    agreement_bonus = (agreement_count - 1) * 8  # 2개 일치 +8, 3개 일치 +16

    # 점수가 중간에서 벗어날수록 기본 신뢰도 상승
    base_confidence = abs(total_score - 50) * 1.5

    # 데이터 품질 반영
    quality = score_detail.get("_quality", {})
    quality_factor = np.mean(list(quality.values())) if quality else 0.7

    confidence = int((base_confidence + agreement_bonus) * quality_factor)
    return max(5, min(95, confidence))


def generate_reasons(features: dict, score_detail: dict, action: str) -> list[str]:
    reasons = []
    t = features["technical"]
    f = features["fundamental"]
    n = features["news"]
    m = features["macro"]

    mom = score_detail.get("momentum_score", 50)
    val = score_detail.get("value_score", 50)
    sent = score_detail.get("sentiment_score", 50)

    # 전략별 상태 요약 (가장 두드러진 것 먼저)
    strategy_summary = []
    if mom > 65:
        strategy_summary.append(f"기술적 모멘텀 강세 (점수 {mom:.0f})")
    elif mom < 35:
        strategy_summary.append(f"기술적 지표 약세 (점수 {mom:.0f})")

    if val > 65:
        strategy_summary.append(f"펀더멘털 우량 (점수 {val:.0f})")
    elif val < 35:
        strategy_summary.append(f"펀더멘털 부진 (점수 {val:.0f})")

    if sent > 65:
        strategy_summary.append(f"시장 감성 긍정적 (점수 {sent:.0f})")
    elif sent < 35:
        strategy_summary.append(f"시장 감성 부정적 (점수 {sent:.0f})")

    reasons.extend(strategy_summary[:2])

    # 세부 시그널
    if t.get("ma20_position", 0) > 0.05:
        reasons.append("20일 이동평균 상회 — 단기 상승 모멘텀")
    elif t.get("ma20_position", 0) < -0.05:
        reasons.append("20일 이동평균 하회 — 단기 하방 압력")

    if t.get("volume_growth_rate", 0) > 0.3:
        reasons.append(f"거래량 급증 ({t['volume_growth_rate']*100:.0f}%) — 관심도 상승")

    rsi = t.get("rsi")
    if rsi is not None:
        if rsi < 30:
            reasons.append(f"RSI {rsi:.1f} — 과매도, 반등 가능성")
        elif rsi > 70:
            reasons.append(f"RSI {rsi:.1f} — 과매수, 조정 주의")

    macd_h = t.get("macd_histogram")
    macd_h_prev = t.get("macd_histogram_prev")
    if macd_h is not None and macd_h_prev is not None:
        if macd_h > 0 and macd_h_prev <= 0:
            reasons.append("MACD 골든크로스 — 상승 전환 신호")
        elif macd_h < 0 and macd_h_prev >= 0:
            reasons.append("MACD 데드크로스 — 하락 전환 신호")

    obv = t.get("obv_trend")
    if obv is not None and abs(obv) > 0.2:
        reasons.append(f"OBV {'상승' if obv > 0 else '하락'} 추세 — {'매집' if obv > 0 else '분산'} 신호")

    if f.get("roe") and f["roe"] > 0.15:
        reasons.append(f"ROE {f['roe']*100:.1f}% — 높은 자본 효율성")

    if f.get("per_relative") and f["per_relative"] < 15:
        reasons.append(f"PER {f['per_relative']:.1f}배 — 저평가 구간")

    sentiment_w = n.get("sentiment_weighted", n.get("sentiment_avg", 0))
    sentiment_mom = n.get("sentiment_momentum", 0)
    if sentiment_w > 0.2:
        reasons.append(f"뉴스 감성 긍정 (가중 점수 {sentiment_w:.2f})")
    elif sentiment_w < -0.2:
        reasons.append(f"뉴스 감성 부정 (가중 점수 {sentiment_w:.2f})")

    if sentiment_mom > 0.1:
        reasons.append("뉴스 감성 개선 추세 — 최근 7일 긍정도 상승")
    elif sentiment_mom < -0.1:
        reasons.append("뉴스 감성 악화 추세 — 최근 7일 부정도 증가")

    vix = m.get("vix")
    if vix and vix > 25:
        reasons.append(f"VIX {vix:.1f} — 시장 변동성 높음")

    if not reasons:
        label = {"BUY": "매수", "WATCH": "관심", "SELL": "청산", "AVOID": "회피"}.get(action, "관심")
        reasons.append(f"복합 지표 종합 기준 {label} 시그널")

    return reasons[:5]
