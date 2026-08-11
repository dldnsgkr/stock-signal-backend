#!/bin/bash
# 2026-08-20 재검증 일괄 실행 — 모멘텀 v2.1 실전 검증 + 과열 감점 배포 결정.
#
# 두 안건 모두 "v2.1 배포(07-27) 이후 성숙한 런이 5개뿐" 이라 결론을 못 냈던 것이다.
# 8/20 이면 07-27~08-13 런이 7일 성숙해 US 약 14런이 된다.
#
# ⚠️ 구간 경계가 시장마다 다르다 (2026-08-10 실측).
#   저장된 점수는 그때 배포돼 있던 스코어러가 만든 것이라, 현재 코드로 재채점하면
#   그 이전 구간은 어긋난다. 자기검증 재현율로 확인한 유효 구간:
#     06월 이전  : 어느 스코어러로도 재현 안 됨 → 버린다
#     07-01~07-26: v2.0 동결 사본으로 100%  (--scorer v20)
#     US 07-27~  : 현재 코드로 100%          (그날 배포가 US 런보다 앞섬)
#     KR 07-28~  : 현재 코드로 100%          (07-27 KR 런은 배포 전에 실행됨 → 80.7%)
#
# 사용법
#   cd ~/stock-signal-backend/apps/analysis-service
#   export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
#   bash scripts/verify_0820.sh 2>&1 | tee /tmp/verify_0820.log

set -u
# 스크립트 위치에 의존하지 않는다 — /tmp 에 복사해 돌리면 dirname/.. 가 루트로 잡혀
# .venv 를 못 찾는다(예행 실행에서 실제로 걸렸다). 저장소 경로를 명시하고,
# 다른 곳에 두고 싶으면 ANALYSIS_HOME 으로 넘긴다.
ROOT="${ANALYSIS_HOME:-$HOME/stock-signal-backend/apps/analysis-service}"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "analysis-service 를 찾을 수 없습니다: $ROOT (ANALYSIS_HOME 으로 지정하세요)" >&2
  exit 1
fi
cd "$ROOT"
PY=".venv/bin/python"
export PYTHONPATH="$PWD:$PWD/scripts"

US_FROM=2026-07-27
KR_FROM=2026-07-28
A_FROM=2026-07-01
A_TO=2026-07-26

hr() { printf '%s\n' "────────────────────────────────────────────────────────────────────────"; }

# 결과에서 판단에 필요한 줄만 뽑는다. 자기검증이 깨지면 그 줄이 경고를 달고 나온다.
run() {
  local label="$1"; shift
  local out
  out=$("$PY" "$@" 2>&1)
  local verify delta guard
  verify=$(printf '%s\n' "$out" | grep -E "자기검증" | head -1 | sed 's/^ *//')
  delta=$(printf '%s\n' "$out"  | grep -E "→ .* - .* :" | head -1 | sed 's/^ *//')
  guard=$(printf '%s\n' "$out"  | grep -cE "검증 실패|결과를 믿지 말 것")
  printf '%-34s %s\n' "$label" "${delta:-(델타 없음 — 아래 원문 확인)}"
  printf '%-34s %s\n' "" "${verify:-(자기검증 줄 없음)}"
  if [ "$guard" -gt 0 ]; then
    printf '%-34s ⚠️  검증 실패 — 이 줄의 결과는 쓰지 말 것\n' ""
  fi
  printf '%s\n' "$out" > "/tmp/v0820_$(echo "$label" | tr ' /·' '___').txt"
}

echo "=== 1. 모멘텀 v2.1 실전 검증 (v2.0 대조) ==="
echo "  판단: 런내 알파가 양수로 굳는가. 08-07 1차는 런 3개로 US +0.22%p / KR -0.18%p 중립이었다."
hr
run "US 임계값65" scripts/counterfactual_momentum.py --market US --from "$US_FROM" --horizon 7d
run "US top20"    scripts/counterfactual_momentum.py --market US --from "$US_FROM" --horizon 7d --top-n 20
run "KR 임계값65" scripts/counterfactual_momentum.py --market KR --from "$KR_FROM" --horizon 7d
hr

echo
echo "=== 2. 과열 감점 강화 배포 결정 ==="
echo "  08-10 판정: US·임계값65 만 통과(A +0.213%p / B +0.073%p). B 가 5런뿐이라 보류했다."
echo "  이번엔 B 구간 표본이 커진다. A 구간은 고정이므로 재확인용으로만 돌린다."
hr
run "US A구간 strong" scripts/counterfactual_overheat.py --market US --scorer v20 --from "$A_FROM" --to "$A_TO" --penalty strong
run "US B구간 strong" scripts/counterfactual_overheat.py --market US --from "$US_FROM" --penalty strong
run "US B구간 veto"   scripts/counterfactual_overheat.py --market US --from "$US_FROM" --penalty veto
run "KR B구간 strong" scripts/counterfactual_overheat.py --market KR --from "$KR_FROM" --penalty strong
hr

cat <<'NOTE'

판단 기준 (CLAUDE.md '신호는 3단계를 통과해야 한다')
  1) 효과   — 델타가 양수인가
  2) 아티팩트 — 과열 감점은 트리거 비율 대조가 스크립트에 내장돼 있다
  3) 재현   — A구간과 B구간의 **부호가 같은가**. 여기서 갈리면 기각한다

주의
  · 실운영 선택 규칙은 **임계값 65** 다. top-N 이 좋아도 그것만으로 배포하지 않는다.
  · 과열 감점은 종목을 빼기만 한다 — 시그널 수가 줄어드는 제품 트레이드오프가 있다.
  · 자기검증이 95% 미만인 줄은 결과를 쓰지 말 것.

원문은 /tmp/v0820_*.txt 에 저장했다.
NOTE
