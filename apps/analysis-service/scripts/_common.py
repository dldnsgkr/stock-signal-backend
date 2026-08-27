"""
반사실(counterfactual) 재채점 스크립트 공용 부품.

두 arm(현행 vs 대안)을 같은 기간·같은 종목 스냅샷으로 재채점해 실현 수익률로
비교하는 스크립트들이 공유한다. 기간을 나눠 비교하면 시장 국면이 교란하므로
(2026-07-31 확인: 겉보기 개선의 ~88%가 국면 효과였다) 항상 이 구조를 쓴다.
"""

import sys
from contextlib import contextmanager
from datetime import date


class Acc:
    """선택된 종목들의 실현 수익률 누적기."""

    def __init__(self, label: str):
        self.label = label
        self.n = 0
        self.ret_sum = 0.0
        self.alpha_sum = 0.0
        self.alpha_n = 0
        self.hits = 0

    def add(self, ret, alpha):
        if ret is None:
            return
        self.n += 1
        self.ret_sum += ret
        if ret > 0:
            self.hits += 1
        if alpha is not None:
            self.alpha_sum += alpha
            self.alpha_n += 1

    @property
    def avg_ret(self):
        return self.ret_sum / self.n if self.n else None

    @property
    def avg_alpha(self):
        return self.alpha_sum / self.alpha_n if self.alpha_n else None

    @property
    def hit_rate(self):
        return self.hits / self.n if self.n else None

    def row(self):
        hit = f"{self.hit_rate * 100:5.1f}%" if self.hit_rate is not None else "    -"
        return (f"  {self.label:<22} n={self.n:>7,}  ret={pct(self.avg_ret)}  "
                f"alpha={pct(self.avg_alpha)}  hit={hit}")


def pct(v):
    return f"{v * 100:+7.3f}%" if v is not None else "      -"


def select(scored, threshold, top_n):
    """(key, score, ret, alpha) 목록에 선택 규칙 적용 → (key 집합, 선택 항목).

    top_n 은 동점이 흔하다(점수가 소수 2자리로 반올림되고, 특히 KR 은 모멘텀
    단독 채점 종목이 많아 같은 점수가 몰린다). 조회 SQL 에 ORDER BY 가 없어
    행 순서가 실행마다 달라지므로, 동점은 반드시 stock_id 로 결정적으로 끊는다.
    안 그러면 같은 명령이 실행마다 다른 수치를 낸다(2026-07-31 실제로 겪음).
    """
    if top_n:
        picked = sorted(scored, key=lambda x: (-x[1], x[0]))[:top_n]
    else:
        picked = [x for x in scored if x[1] >= threshold]
    return {x[0] for x in picked}, picked


def add_common_args(ap, default_market="US"):
    ap.add_argument("--market", default=default_market, choices=["US", "KR"])
    ap.add_argument("--from", dest="fromdate", default=None, help="YYYY-MM-DD (포함)")
    ap.add_argument("--to", dest="todate", default=None, help="YYYY-MM-DD (포함)")
    # 1d 는 7일 성숙 전 조기 확인용. 노이즈가 크므로 방향 참고로만 쓸 것.
    ap.add_argument("--horizon", default="7d", choices=["1d", "7d", "30d"])
    ap.add_argument("--top-n", type=int, default=None,
                    help="런당 상위 N종목 선택. 미지정 시 임계값 기준")
    ap.add_argument("--threshold", type=float, default=None, help="BUY 임계값")
    ap.add_argument("--max-abs-ret", type=float, default=1.0,
                    help="이상치 제외: |수익률| 이 이 값을 넘으면 버림 (0 이면 미적용)")
    ap.add_argument("--dsn", default=None, help="기본값: 환경변수 DATABASE_URL")
    return ap


def resolve_dsn(arg, env):
    dsn = arg or env.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL 이 없습니다. --dsn 으로 주거나 환경변수를 설정하세요.")
    return dsn.strip().strip('"').replace("postgresql+asyncpg://", "postgresql://")


def parse_dates(fromdate, todate):
    """asyncpg 는 ::date 파라미터에 문자열을 받지 않는다 (date 객체여야 함)."""
    try:
        return (date.fromisoformat(fromdate) if fromdate else None,
                date.fromisoformat(todate) if todate else None)
    except ValueError as e:
        sys.exit(f"날짜 형식 오류 (YYYY-MM-DD 여야 합니다): {e}")


def report(title, meta_lines, arm_a, arm_b, both, only_a, only_b, overlap, per_run):
    """두 arm 비교 결과를 표로 출력한다. arm_b - arm_a 를 델타로 본다."""
    width = 88
    print()
    print("=" * width)
    print(f" {title}")
    for line in meta_lines:
        print(f" {line}")
    print("=" * width)
    if overlap:
        print(f" 선택 중복도(Jaccard) 평균 {sum(overlap) / len(overlap) * 100:.1f}%")
    print("-" * width)
    print(" 전체 선택 집합")
    print(arm_a.row())
    print(arm_b.row())
    if arm_a.avg_alpha is not None and arm_b.avg_alpha is not None:
        d_alpha = (arm_b.avg_alpha - arm_a.avg_alpha) * 100
        d_hit = (arm_b.hit_rate - arm_a.hit_rate) * 100
        print(f"  → {arm_b.label} - {arm_a.label} :  alpha {d_alpha:+.3f}%p   적중률 {d_hit:+.2f}%p")
    print("-" * width)
    print(" 차이가 나는 부분만 (두 설정이 갈라선 종목 — 여기가 실제 효과)")
    print(both.row())
    print(only_a.row())
    print(only_b.row())
    if only_a.avg_alpha is not None and only_b.avg_alpha is not None:
        print(f"  → 단독 선택끼리 alpha 차 {(only_b.avg_alpha - only_a.avg_alpha) * 100:+.3f}%p")
    print("-" * width)
    print(" 런별 (alpha)")
    print(f"  {'실행일':<12} {'비고':<16} {'채점':>7} {'A n':>7} {'A α':>9} {'B n':>7} {'B α':>9}")
    for executed_at, note, scored_n, n_a, a_a, n_b, a_b in per_run:
        f_a = f"{a_a * 100:+.3f}%" if a_a is not None else "-"
        f_b = f"{a_b * 100:+.3f}%" if a_b is not None else "-"
        print(f"  {executed_at.strftime('%Y-%m-%d'):<12} {note:<16} {scored_n:>7,} "
              f"{n_a:>7,} {f_a:>9} {n_b:>7,} {f_b:>9}")
    print("=" * width)
    print()


# ── 스코어러 arm + 자기검증 ────────────────────────────────────────────────
#
# 저장된 점수는 **그때 배포돼 있던 스코어러**가 만든 것이라, 현재 코드로 옛 구간을
# 재채점하면 어긋난다. 2026-08-10 실측 재현율(US):
#     06월 이전   0.9~79.6%  → 무엇으로도 재현 안 됨, 버린다
#     07-01~07-26 100%       → v2.0 동결 사본 (--scorer v20)
#     07-27~08-25 100%       → v2.1 동결 사본 (--scorer v21, KR 은 07-28~)
#     08-26~                 → 현재 코드 (v2.2 과열 감점)
# ⚠️ 2026-08-26 v2.2 배포로 경계가 하나 더 생겼다 — 그 전에는 07-27~ 가
# 'current' 였다. v2.1 구간을 current 로 재채점하면 과열 감점 때문에 어긋난다.

WINDOWS = {
    "A": ("v20", "2026-07-01", "2026-07-26"),   # v2.0 구간
    "US_B": ("v21", "2026-07-27", "2026-08-25"),  # v2.1 구간 (US)
    "KR_B": ("v21", "2026-07-28", "2026-08-25"),  # v2.1 구간 (KR — 07-27 런은 배포 전)
    "C": ("current", "2026-08-26", None),       # v2.2 구간
}

_FROZEN = {
    "v20": ("scorer_v20_frozen", "momentum_score_v20"),
    "v21": ("scorer_v21_frozen", "momentum_score_v21"),
}


def add_scorer_arg(ap):
    ap.add_argument("--scorer", choices=["current", "v20", "v21"], default="current",
                    help="구간에 맞출 것 — v20: ~07-26 / v21: 07-27~08-25 / current(v2.2): 08-26~")
    return ap


@contextmanager
def scorer_arm(arm):
    """블록 안에서만 `_momentum_score` 를 해당 버전 동결 사본으로 바꾼다.

    arm: 'current'|'v20'|'v21' (하위호환 — bool 이 오면 True='v20', False='current')
    """
    if isinstance(arm, bool):
        arm = "v20" if arm else "current"
    if arm == "current":
        yield
        return
    import importlib

    from app.engine import scorer
    mod_name, fn_name = _FROZEN[arm]
    frozen = getattr(importlib.import_module(mod_name), fn_name)
    original = scorer._momentum_score
    scorer._momentum_score = frozen
    try:
        yield
    finally:
        scorer._momentum_score = original


class Verifier:
    """재채점값이 저장된 점수를 재현하는지 센다.

    **결과를 믿기 전에 반드시 확인할 것** — 스코어러가 바뀐 구간을 잘못 잡으면
    조용히 틀린 답이 나온다. 95% 미만이면 그 실행은 버린다.
    """

    def __init__(self, tol: float = 0.011):
        self.tol = tol
        self.ok = 0
        self.n = 0

    def check(self, stored, rescored):
        if stored is None:
            return
        self.n += 1
        if abs(float(stored) - rescored) < self.tol:
            self.ok += 1

    @property
    def rate(self):
        return self.ok / self.n * 100 if self.n else 0.0

    def lines(self):
        # 표본 0 을 '재현율 0%' 로 쓰면 안 된다 — 원인이 전혀 다르다.
        # (모집단 필터가 전부 걸러낸 경우가 있다. KR 가치 스윕을 7월 구간에 돌리면
        #  그때는 PER/PBR 이 아예 없어서 0건이 되는데, 이건 재현 실패가 아니다.)
        if self.n == 0:
            return ["⚠️ 자기검증 불가 — 대조할 행이 0건이다 (모집단 필터·기간 확인)"]
        out = [f"자기검증: 재채점이 저장 점수를 재현 {self.rate:.1f}% ({self.ok:,}/{self.n:,})"]
        if self.rate < 95:
            out.append("⚠️ 자기검증 실패 — 이 결과는 쓰지 말 것 (구간/스코어러 확인)")
        return out
