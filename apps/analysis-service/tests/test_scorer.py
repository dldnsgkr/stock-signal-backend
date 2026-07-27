"""스코어링 엔진 단위 테스트.

조용히 전 종목을 오채점할 수 있는 지점을 지킨다:
  - determine_action 임계값 경계
  - 데이터 품질 적응형 가중치 재분배 (합=1, 결측 전략 비중 소멸)
  - 컴포넌트 점수 0~100 클램프
  - confidence 5~95 클램프
"""
import math
import pytest

from app.engine.scorer import (
    calculate_total_score,
    determine_action,
    calculate_confidence,
    _momentum_score,
    _value_score,
    _sentiment_score,
    BUY_THRESHOLD,
    WATCH_THRESHOLD,
)


def make_features(technical=None, fundamental=None, news=None, macro=None):
    return {
        "technical": technical or {},
        "fundamental": fundamental or {},
        "news": news or {},
        "macro": macro or {},
    }


# ── determine_action 임계값 경계 ──────────────────────────────────────────
@pytest.mark.parametrize("score,expected", [
    (100, "BUY"),
    (65, "BUY"),          # 경계: BUY_THRESHOLD 포함
    (64.99, "WATCH"),
    (45, "WATCH"),        # 경계: WATCH_THRESHOLD 포함
    (44.99, "AVOID"),
    (0, "AVOID"),
])
def test_determine_action_boundaries(score, expected):
    assert determine_action(score) == expected


def test_thresholds_ordering():
    assert WATCH_THRESHOLD < BUY_THRESHOLD


# ── 컴포넌트 점수 0~100 클램프 ────────────────────────────────────────────
def test_momentum_score_clamped_high():
    strong = {
        "ma20_position": 1.0, "ma60_position": 1.0, "momentum_5d": 1.0,
        "momentum_20d": 1.0, "volume_growth_rate": 1.0, "rsi": 20,
        "macd_histogram": 1.0, "macd_histogram_prev": -1.0,
        "bb_position": -0.5, "obv_trend": 1.0,
    }
    s, q = _momentum_score(make_features(technical=strong))
    assert 0.0 <= s <= 100.0
    assert s > 80.0            # 강세 신호는 높은 점수
    assert 0.0 <= q <= 1.0


def test_momentum_score_clamped_low():
    weak = {
        "ma20_position": -1.0, "ma60_position": -1.0, "momentum_5d": -1.0,
        "momentum_20d": -1.0, "volume_growth_rate": -1.0, "rsi": 80,
        "macd_histogram": -1.0, "macd_histogram_prev": 1.0,
        "bb_position": 1.5, "obv_trend": -1.0,
    }
    s, _ = _momentum_score(make_features(technical=weak))
    assert 0.0 <= s <= 100.0
    assert s < 30.0


def test_value_score_high_quality_fundamentals():
    good = {"roe": 0.25, "per_relative": 8, "pbr_relative": 0.8}
    s, q = _value_score(make_features(fundamental=good))
    assert s > 80.0
    assert q == pytest.approx(1.0)      # 3개 지표 모두 존재


def test_value_score_missing_all_is_neutral_zero_quality():
    s, q = _value_score(make_features())
    assert s == pytest.approx(50.0)     # 데이터 없으면 중립
    assert q == 0.0


def test_sentiment_high_vix_penalized():
    calm, _ = _sentiment_score(make_features(macro={"vix": 12}))
    panic, _ = _sentiment_score(make_features(macro={"vix": 40}))
    assert panic < calm                 # 고VIX는 감점


# ── 적응형 가중치 재분배 ──────────────────────────────────────────────────
def test_weights_normalized_sum_to_one():
    detail = calculate_total_score(make_features(
        technical={"ma20_position": 0.1},
        fundamental={"roe": 0.2},
        news={"sentiment_weighted": 0.3},
        macro={"vix": 18},
    ))
    w = detail["_weights"]
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_missing_fundamental_redistributes_weight():
    # 펀더멘털 데이터가 전혀 없으면 value 비중이 0 으로 소멸하고
    # momentum/sentiment 로 재분배돼야 한다.
    detail = calculate_total_score(make_features(
        technical={"ma20_position": 0.1, "rsi": 40},
        news={"sentiment_weighted": 0.3},
        macro={"vix": 18},
    ))
    w = detail["_weights"]
    assert w["value"] == pytest.approx(0.0, abs=1e-9)
    assert w["momentum"] > 0
    assert w["sentiment"] > 0
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_total_score_within_range_and_rounded():
    detail = calculate_total_score(make_features(
        technical={"ma20_position": 0.1}, fundamental={"roe": 0.2},
        news={"sentiment_weighted": 0.2}, macro={"vix": 15},
    ))
    ts = detail["total_score"]
    assert 0.0 <= ts <= 100.0
    assert round(ts, 2) == ts           # 소수 2자리 반올림


def test_custom_base_weights_applied():
    # 백테스트용 커스텀 가중치가 반영되는지 (momentum 100%)
    feats = make_features(
        technical={"ma20_position": 0.1, "rsi": 25},
        fundamental={"roe": -0.5},      # 나쁜 펀더멘털
        news={}, macro={},
    )
    mom_only = calculate_total_score(feats, base_weights={"momentum": 1.0, "value": 0.0, "sentiment": 0.0})
    # value 가중치 0 이면 나쁜 펀더멘털이 총점에 영향 없어야
    assert mom_only["_weights"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert mom_only["total_score"] == pytest.approx(mom_only["momentum_score"], abs=0.01)


# ── confidence 클램프 ─────────────────────────────────────────────────────
def test_confidence_clamped_range():
    detail = calculate_total_score(make_features(
        technical={"ma20_position": 0.1}, fundamental={"roe": 0.2},
        news={"sentiment_weighted": 0.2}, macro={"vix": 15},
    ))
    c = calculate_confidence(detail["total_score"], detail)
    assert 5 <= c <= 95
    assert isinstance(c, int)


def test_confidence_agreement_raises_confidence():
    # 세 전략 모두 강세(>55) → 일치 보너스로 신뢰도 상승
    agree = {"momentum_score": 80, "value_score": 80, "sentiment_score": 80,
             "_quality": {"momentum": 1, "value": 1, "sentiment": 1}}
    mixed = {"momentum_score": 80, "value_score": 40, "sentiment_score": 52,
             "_quality": {"momentum": 1, "value": 1, "sentiment": 1}}
    assert calculate_confidence(80, agree) > calculate_confidence(80, mixed)
