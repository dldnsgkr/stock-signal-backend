"""
재무 지표 파싱·파생의 순수 계산부.

`financial_collector` 에서 분리해 둔 이유는 **테스트 때문**이다. CI 는
`pip install numpy pytest` 만 하고 pytest 를 돌린다(test.yml). 수집기 본체는
yfinance·sqlalchemy·DB 모델을 끌고 오므로 그대로 import 하면 CI 에서 죽는다.
네트워크도 DB 도 타지 않는 계산만 여기 모아 두면 의존성 없이 검증할 수 있다.
(스코어링 테스트가 numpy 만 쓰는 scorer.py 를 겨냥하는 것과 같은 구조다.)
"""

# 파생 PER/PBR 상한. 이 위는 분모가 0 에 가까워 생긴 노이즈로 보고 버린다.
PER_MAX = 1000.0
PBR_MAX = 100.0

# 자기자본 행 이름은 티커마다 다르다. 앞쪽을 우선한다.
EQUITY_KEYS = ("Stockholders Equity", "Common Stock Equity",
               "Total Equity Gross Minority Interest")


def safe_float(value) -> float | None:
    """비율 지표용 (ROE·PER·PBR·부채비율). 1e12 상한은 이상치 방어다."""
    try:
        if value is None or value != value:  # NaN check
            return None
        f = float(value)
        if f == 0.0 or abs(f) > 1e12:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def safe_amount(value) -> float | None:
    """
    통화 금액용 (매출·순이익·영업이익·시총).

    `safe_float` 의 1e12 상한을 쓰면 **원화 대형주가 통째로 잘린다** — 삼성전자
    시총 약 1.7e15 KRW, 순이익 약 1.5e14 KRW 다. 실제로 이 상한 때문에 저장된
    net_income 최대값이 9,671억(1e12 바로 아래)에 걸려 있었고, KR 대형주는
    매출·순이익이 전부 NULL 이었다(2026-08-03 발견). 이 컬럼들이 스코어링에
    쓰이지 않아 드러나지 않았을 뿐이다.

    상한은 통화 단위와 무관하게 이상치만 걸러내도록 훨씬 크게 잡되,
    저장 컬럼이 Decimal(20,2)(정수부 18자리)라 그 안에 들어오도록 1e17 로 둔다.
    삼성전자 매출 약 4.9e14 KRW 보다 200배 여유가 있다.
    """
    try:
        if value is None or value != value:  # NaN check
            return None
        f = float(value)
        if f == 0.0 or abs(f) > 1e17:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def derive_ratio(market_cap, denominator, cap: float) -> float | None:
    """
    시총 ÷ 분모 로 PER/PBR 을 만든다 (PER 분모=순이익, PBR 분모=자기자본).

    분모가 0 이하면 지표 자체가 의미 없으므로 None (적자 기업의 PER, 자본잠식의 PBR).
    상한을 넘는 값도 분모가 0 에 가까워 생긴 노이즈라 버린다.
    """
    if market_cap is None or denominator is None:
        return None
    if market_cap <= 0 or denominator <= 0:
        return None
    ratio = market_cap / denominator
    if ratio <= 0 or ratio > cap:
        return None
    return round(ratio, 4)


def extract_equity(balance_sheet) -> float | None:
    """
    yfinance `balance_sheet` DataFrame 에서 가장 최근 연차 자기자본을 꺼낸다.

    pandas 를 import 하지 않고 index/columns/loc 만 덕 타이핑으로 쓴다 —
    테스트에서 가짜 객체를 넣을 수 있고 CI 의존성도 늘지 않는다.
    """
    if balance_sheet is None or getattr(balance_sheet, "empty", True):
        return None
    row = next((k for k in EQUITY_KEYS if k in balance_sheet.index), None)
    if row is None:
        return None
    # 컬럼은 결산일이고 최신이 앞에 오지만, 순서를 가정하지 않고 직접 고른다.
    best_col = max(balance_sheet.columns)
    return safe_amount(balance_sheet.loc[row][best_col])
