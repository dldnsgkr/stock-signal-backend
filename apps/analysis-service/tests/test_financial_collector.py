"""재무 수집기의 PER/PBR 폴백 단위 테스트.

여기서 지키는 것 (조용히 전 종목의 가치 점수를 망칠 수 있는 지점들):
  - 원화 대형주가 금액 상한에 잘리지 않는가 (1e12 상한 때문에 KR 대형주
    매출·순이익이 전부 NULL 이던 버그. 이게 재발하면 PER 폴백도 같이 죽는다)
  - 적자·자본잠식 기업에 엉터리 PER/PBR 을 만들지 않는가
  - 자기자본 행 이름이 티커마다 달라도 뽑아내는가
"""
import pytest

from app.collectors.financial_collector import (
    PBR_MAX,
    PER_MAX,
    _derive_ratio,
    _extract_equity,
    _safe_amount,
    _safe_float,
)


class FakeBalanceSheet:
    """yfinance balance_sheet DataFrame 의 최소 대역 (index/columns/loc)."""

    def __init__(self, data: dict, columns: list):
        self._data = data
        self.index = list(data.keys())
        self.columns = columns
        self.empty = not data

    @property
    def loc(self):
        return self._data


# ── 금액 파서 ────────────────────────────────────────────────────────────
def test_safe_amount_keeps_krw_large_cap():
    """삼성전자급 원화 금액(1e14~1e15)이 살아남아야 한다."""
    assert _safe_amount(149_733_733_564_416) == 149733733564416.0
    assert _safe_amount(1_723_722_847_223_808) == 1723722847223808.0


def test_safe_float_still_caps_ratios():
    """비율 지표는 기존대로 1e12 에서 막는다 (이상치 방어)."""
    assert _safe_float(1e13) is None
    assert _safe_float(35.4) == 35.4


def test_safe_amount_rejects_none_zero_and_absurd():
    assert _safe_amount(None) is None
    assert _safe_amount(0) is None
    assert _safe_amount(float("nan")) is None
    assert _safe_amount(1e19) is None       # Decimal(20,2) 를 넘김
    assert _safe_amount("abc") is None


# ── PER/PBR 파생 ─────────────────────────────────────────────────────────
def test_derive_ratio_basic():
    # 시총 1,000 / 순이익 100 → PER 10
    assert _derive_ratio(1000.0, 100.0, PER_MAX) == 10.0


def test_derive_ratio_krw_scale():
    """원화 대형주에서도 정상적인 배수가 나와야 한다."""
    per = _derive_ratio(1_723_722_847_223_808, 149_733_733_564_416, PER_MAX)
    assert per is not None
    assert 10 < per < 13


@pytest.mark.parametrize("denominator", [-100.0, 0.0, None])
def test_derive_ratio_rejects_nonpositive_denominator(denominator):
    """적자 기업의 PER, 자본잠식 기업의 PBR 은 지표 자체가 무의미하다."""
    assert _derive_ratio(1000.0, denominator, PER_MAX) is None


def test_derive_ratio_rejects_missing_market_cap():
    assert _derive_ratio(None, 100.0, PER_MAX) is None
    assert _derive_ratio(-1.0, 100.0, PER_MAX) is None


def test_derive_ratio_rejects_above_cap():
    """분모가 0 에 가까워 생기는 노이즈는 버린다."""
    assert _derive_ratio(1000.0, 0.5, PER_MAX) is None      # 2000 > 1000
    assert _derive_ratio(1000.0, 5.0, PBR_MAX) is None      # 200 > 100
    assert _derive_ratio(1000.0, 20.0, PBR_MAX) == 50.0     # 상한 이내


# ── 자기자본 추출 ────────────────────────────────────────────────────────
def test_extract_equity_prefers_stockholders_equity():
    bs = FakeBalanceSheet(
        {"Stockholders Equity": {"2025-12-31": 500.0},
         "Common Stock Equity": {"2025-12-31": 400.0}},
        ["2025-12-31"],
    )
    assert _extract_equity(bs) == 500.0


def test_extract_equity_falls_back_when_row_name_differs():
    """삼성전자는 'Stockholders Equity' 행이 없고 'Common Stock Equity' 만 있다."""
    bs = FakeBalanceSheet(
        {"Common Stock Equity": {"2025-12-31": 424_193_788_000_000.0}},
        ["2025-12-31"],
    )
    assert _extract_equity(bs) == 424193788000000.0


def test_extract_equity_picks_latest_period():
    bs = FakeBalanceSheet(
        {"Stockholders Equity": {"2023-12-31": 100.0, "2025-12-31": 300.0,
                                 "2024-12-31": 200.0}},
        ["2023-12-31", "2025-12-31", "2024-12-31"],   # 순서를 가정하지 않는다
    )
    assert _extract_equity(bs) == 300.0


def test_extract_equity_handles_missing_and_empty():
    assert _extract_equity(None) is None
    assert _extract_equity(FakeBalanceSheet({}, [])) is None
    assert _extract_equity(FakeBalanceSheet({"Total Assets": {"2025-12-31": 1.0}},
                                            ["2025-12-31"])) is None
