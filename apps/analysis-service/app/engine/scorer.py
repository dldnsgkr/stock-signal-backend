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
from typing import Optional

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

    # ── 과열 추격 추가 감점 (ensemble_v2.2, 2026-08-26 배포) ──────────────────
    #
    # 왜: 점수-알파 곡선이 ∩자이고 85점 위에서 절벽이었다(US 55~65 +0.346%
    # → 90+ **-4.878%**, 적중 31.1%). 원인은 **가점 항의 중복 계산** — MA20 위치
    # +15, MA60 +10, MACD +8/+7, OBV +8 이 전부 "주가가 올랐다" 는 같은 사실의
    # 함수라, v2.1 의 과열 감점(-8/-15)으로는 그 합 +66 을 못 이겼다.
    # 90+ 안에서 20일 상승폭이 클수록 단조로 나빠진다(<5% -0.67% → >=30% -10.78%).
    #
    # ⚠️ **위 밴드에 병합하지 말고 여기서 더할 것.** 검증이 이 형태(기존 밴드 위에
    # 얹고 **클리핑 전** 적용)로 이뤄졌다. 병합하면 경계가 달라져 검증과 어긋난다.
    # 클리핑 전이어야 하는 이유: 90점 이상의 67.5%(US)·51.5%(KR)가 모멘텀 100 으로
    # 잘려 있어, 사후에 빼면 바로 그 집단의 감점이 과대평가된다.
    #
    # 3단계 검증 통과 (counterfactual_overheat.py, 2026-08-26, 자기검증 100%):
    #   Δalpha(임계값 65) — A구간 07-01~26 / B구간 07-27~
    #     US  +0.213%p / +0.066%p      KR  +0.249%p / +0.090%p   → 양 시장·양 구간 양수
    #   빠지는 종목이 실제로 나쁘다: US 2,422종목 알파 -1.123%(풀 -0.222%),
    #   KR 1,238종목 -1.890%(풀 -0.852%). 대가는 시그널 수 6.9%(US)/8.0%(KR) 감소.
    if mom_20d is not None:
        if mom_20d >= 0.30:
            score -= 25
        elif mom_20d >= 0.20:
            score -= 12
        elif mom_20d >= 0.15:
            score -= 5

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
def calculate_total_score(features: dict, base_weights: Optional[dict] = None) -> dict:
    """base_weights 를 주면 그 가중치로 채점한다. 백테스트(재점수화)에서 쓴다."""
    base = base_weights or BASE_WEIGHTS

    mom_score, mom_q = _momentum_score(features)
    val_score, val_q = _value_score(features)
    sent_score, sent_q = _sentiment_score(features)

    # 데이터 품질 기반 가중치 재분배
    raw_w = {
        "momentum":  base["momentum"]  * mom_q,
        "value":     base["value"]     * val_q,
        "sentiment": base["sentiment"] * sent_q,
    }
    total_w = sum(raw_w.values())
    if total_w == 0:
        weights = base
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
