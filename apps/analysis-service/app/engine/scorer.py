from typing import Optional


WEIGHTS = {
    "technical": 0.35,
    "fundamental": 0.25,
    "news": 0.20,
    "macro": 0.10,
    "flow": 0.10,
}

BUY_THRESHOLD = 65
WATCH_THRESHOLD = 45


def calculate_technical_score(features: dict) -> float:
    t = features["technical"]
    score = 50.0

    ma20_pos = t.get("ma20_position", 0)
    if ma20_pos > 0.05:
        score += 15
    elif ma20_pos > 0:
        score += 8
    elif ma20_pos < -0.05:
        score -= 15
    elif ma20_pos < 0:
        score -= 8

    ma60_pos = t.get("ma60_position", 0)
    if ma60_pos > 0.03:
        score += 10
    elif ma60_pos < -0.03:
        score -= 10

    vol_growth = t.get("volume_growth_rate", 0)
    if vol_growth > 0.5:
        score += 12
    elif vol_growth > 0.2:
        score += 6
    elif vol_growth < -0.3:
        score -= 8

    mom_5d = t.get("momentum_5d", 0)
    if mom_5d > 0.05:
        score += 8
    elif mom_5d > 0.02:
        score += 4
    elif mom_5d < -0.05:
        score -= 8

    mom_20d = t.get("momentum_20d", 0)
    if mom_20d > 0.10:
        score += 10
    elif mom_20d > 0.03:
        score += 5
    elif mom_20d < -0.10:
        score -= 10

    rsi = t.get("rsi")
    if rsi is not None:
        if rsi < 30:
            score += 15  # oversold
        elif rsi < 45:
            score += 8
        elif rsi > 70:
            score -= 12  # overbought
        elif rsi > 60:
            score -= 4

    macd_h = t.get("macd_histogram")
    macd_h_prev = t.get("macd_histogram_prev")
    if macd_h is not None:
        if macd_h > 0:
            score += 8
        else:
            score -= 8
        # 골든/데드크로스
        if macd_h_prev is not None:
            if macd_h > 0 and macd_h_prev <= 0:
                score += 7   # 골든크로스
            elif macd_h < 0 and macd_h_prev >= 0:
                score -= 7   # 데드크로스

    bb_pos = t.get("bb_position")
    if bb_pos is not None:
        if bb_pos < 0:
            score += 12   # 하단 밴드 이탈 (과매도)
        elif bb_pos < 0.2:
            score += 6    # 하단 밴드 근접
        elif bb_pos > 1:
            score -= 10   # 상단 밴드 이탈 (과매수)
        elif bb_pos > 0.8:
            score -= 4    # 상단 밴드 근접

    return max(0.0, min(100.0, score))


def calculate_fundamental_score(features: dict) -> float:
    f = features["fundamental"]
    score = 50.0

    roe = f.get("roe")
    if roe is not None:
        if roe > 0.20:
            score += 20
        elif roe > 0.10:
            score += 10
        elif roe < 0:
            score -= 20

    per = f.get("per_relative")
    if per is not None:
        if 0 < per < 15:
            score += 15
        elif 15 <= per < 25:
            score += 5
        elif per > 50:
            score -= 10

    pbr = f.get("pbr_relative")
    if pbr is not None:
        if 0 < pbr < 1.5:
            score += 10
        elif pbr > 5:
            score -= 5

    return max(0.0, min(100.0, score))


def calculate_news_score(features: dict) -> float:
    n = features["news"]
    score = 50.0

    sentiment = n.get("sentiment_avg", 0)
    if sentiment > 0.3:
        score += 20
    elif sentiment > 0.05:
        score += 10
    elif sentiment < -0.3:
        score -= 20
    elif sentiment < -0.05:
        score -= 10

    pos_count = n.get("positive_count", 0)
    neg_count = n.get("negative_count", 0)
    if pos_count > neg_count * 2:
        score += 10
    elif neg_count > pos_count * 2:
        score -= 15

    if n.get("news_frequency_spike", False):
        if sentiment > 0:
            score += 5
        else:
            score -= 5

    return max(0.0, min(100.0, score))


def calculate_macro_score(features: dict) -> float:
    m = features["macro"]
    score = 60.0

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

    return max(0.0, min(100.0, score))


def calculate_flow_score(features: dict) -> float:
    fl = features["flow"]
    score = 50.0

    tv_growth = fl.get("trading_value_growth", 0)
    if tv_growth > 0.5:
        score += 15
    elif tv_growth > 0.2:
        score += 8
    elif tv_growth < -0.3:
        score -= 10

    return max(0.0, min(100.0, score))


def calculate_total_score(features: dict) -> dict:
    technical_score = calculate_technical_score(features)
    fundamental_score = calculate_fundamental_score(features)
    news_score = calculate_news_score(features)
    macro_score = calculate_macro_score(features)
    flow_score = calculate_flow_score(features)

    total = (
        technical_score * WEIGHTS["technical"]
        + fundamental_score * WEIGHTS["fundamental"]
        + news_score * WEIGHTS["news"]
        + macro_score * WEIGHTS["macro"]
        + flow_score * WEIGHTS["flow"]
    )

    return {
        "technical_score": round(technical_score, 2),
        "fundamental_score": round(fundamental_score, 2),
        "news_score": round(news_score, 2),
        "macro_score": round(macro_score, 2),
        "flow_score": round(flow_score, 2),
        "total_score": round(total, 2),
    }


def determine_action(total_score: float) -> str:
    if total_score >= BUY_THRESHOLD:
        return "BUY"
    elif total_score >= WATCH_THRESHOLD:
        return "WATCH"
    else:
        return "AVOID"


def calculate_confidence(total_score: float, score_detail: dict) -> int:
    scores = [
        score_detail["technical_score"],
        score_detail["fundamental_score"],
        score_detail["news_score"],
    ]
    std = (max(scores) - min(scores)) / 50
    consistency_bonus = max(0, 20 - std * 100)

    base_confidence = abs(total_score - 50) * 1.5
    confidence = min(95, int(base_confidence + consistency_bonus))
    return max(5, confidence)


def generate_reasons(features: dict, score_detail: dict, action: str) -> list[str]:
    reasons = []
    t = features["technical"]
    f = features["fundamental"]
    n = features["news"]
    m = features["macro"]

    if t.get("ma20_position", 0) > 0.05:
        reasons.append("20일 이동평균 상회 - 단기 상승 모멘텀 형성")
    elif t.get("ma20_position", 0) < -0.05:
        reasons.append("20일 이동평균 하회 - 단기 하방 압력")

    if t.get("volume_growth_rate", 0) > 0.3:
        reasons.append(f"거래량 급증 ({t['volume_growth_rate']*100:.0f}% 증가) - 관심도 상승")

    if t.get("momentum_20d", 0) > 0.08:
        reasons.append(f"20일 수익률 {t['momentum_20d']*100:.1f}% - 강한 모멘텀")

    rsi = t.get("rsi")
    if rsi is not None:
        if rsi < 30:
            reasons.append(f"RSI {rsi:.1f} - 과매도 구간, 반등 가능성")
        elif rsi > 70:
            reasons.append(f"RSI {rsi:.1f} - 과매수 구간, 조정 주의")

    macd_h = t.get("macd_histogram")
    macd_h_prev = t.get("macd_histogram_prev")
    if macd_h is not None and macd_h_prev is not None:
        if macd_h > 0 and macd_h_prev <= 0:
            reasons.append("MACD 골든크로스 - 상승 전환 신호")
        elif macd_h < 0 and macd_h_prev >= 0:
            reasons.append("MACD 데드크로스 - 하락 전환 신호")

    bb_pos = t.get("bb_position")
    if bb_pos is not None:
        if bb_pos < 0:
            reasons.append("볼린저 하단 밴드 이탈 - 단기 과매도")
        elif bb_pos > 1:
            reasons.append("볼린저 상단 밴드 이탈 - 단기 과매수")

    if f.get("roe") and f["roe"] > 0.15:
        reasons.append(f"ROE {f['roe']*100:.1f}% - 높은 자본 효율성")

    if f.get("per_relative") and f["per_relative"] < 15:
        reasons.append(f"PER {f['per_relative']:.1f}배 - 상대적 저평가 구간")

    if n.get("sentiment_avg", 0) > 0.2:
        reasons.append(f"뉴스 감성 긍정적 (점수: {n['sentiment_avg']:.2f}) - 시장 관심 증가")
    elif n.get("sentiment_avg", 0) < -0.2:
        reasons.append(f"뉴스 감성 부정적 (점수: {n['sentiment_avg']:.2f}) - 부정적 이슈 존재")

    if m.get("vix") and m["vix"] > 25:
        reasons.append(f"VIX {m['vix']:.1f} - 시장 변동성 높음, 주의 필요")

    if not reasons:
        if action == "BUY":
            reasons.append("복합 지표 종합 점수 기준 매수 시그널")
        elif action == "WATCH":
            reasons.append("복합 지표 종합 점수 기준 관심 종목")
        else:
            reasons.append("현재 지표 기준 투자 매력도 낮음")

    return reasons[:5]
