#!/bin/bash
# 2026-09-01 재검토 일괄 실행 — KR 임계값 + **가치 가중치 구조**(핵심 안건).
#
# 배경
# ----
# 매출성장 YoY(원신호 6.3%p, 3단계 전부 통과)와 부채비율(3.0%p, KR 통과)이 검증을
# 통과하고도 배포 효과가 +0.063%p / +0.028%p 에 그쳤다. 가치 가중치가 ~0.2 라
# 서브스코어 +8 이 총점 +1.6 이고 선택 중복도가 97% 이기 때문이다.
# → **천장은 신호 강도가 아니라 구조다.** 가중치를 올리면 검증된 신호 둘이 살아난다.
#
# ⚠️ 가치 안건은 쓸 수 있는 구간이 **2026-08-11 부터**다 (2026-08-18 확인).
#   KR 스냅샷에 피처가 실제로 실린 날:
#     PER/PBR   08-07 부분(1,939/2,775) → 08-11 부터 full(2,670)
#     매출성장  08-10 부분(1,489)       → 08-11 부터 full(2,417+)
#     부채비율  08-10 부분(1,921)       → 08-11 부터 full(2,125+)
#   그 이전 구간은 가치 데이터가 없어 **가중치를 올려도 품질 재분배로 0** 이 된다.
#   (7월 구간에 스윕을 돌리면 채점 0건이 나온다 — 버그가 아니다)
#
# 임계값 안건은 재채점이 아니라 저장 점수 필터라 전 구간을 쓴다 → 별도 SQL.
#
# 사용법
#   cd ~/stock-signal-backend/apps/analysis-service
#   export $(grep "^DATABASE_URL" ../api/.env | sed 's/"//g')
#   bash scripts/verify_0901.sh 2>&1 | tee /tmp/verify_0901.log

set -u
ROOT="${ANALYSIS_HOME:-$HOME/stock-signal-backend/apps/analysis-service}"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "analysis-service 를 찾을 수 없습니다: $ROOT (ANALYSIS_HOME 으로 지정하세요)" >&2
  exit 1
fi
cd "$ROOT"
PY=".venv/bin/python"
export PYTHONPATH="$PWD:$PWD/scripts"

# 가치 피처가 full 로 실린 첫 런. 위 주석 참조 — 앞당기면 조용히 희석된다.
VAL_FROM=2026-08-11
# v2.1 구간의 끝. 2026-08-26 부터는 v2.2(과열 감점)로 채점돼 있어 v21 사본으로는
# 재현이 안 된다 — 그 뒤 런까지 쓰려면 --scorer current 로 **따로** 돌려야 한다.
VAL_TO=2026-08-25
# 기간 이분 경계. 9/1 기준 08-11~08-25 약 11런이라 각 5~6런이 된다.
SPLIT=2026-08-18

hr() { printf '%s\n' "────────────────────────────────────────────────────────────────────────"; }

save() { printf '%s\n' "$2" > "/tmp/v0901_$(echo "$1" | tr ' /·>=' '_____').txt"; }

# 실행이 성립하지 않은 경우를 **조용히 넘기지 않는다**. 런이 없으면 그렇게 말한다.
noruns() { printf '%s\n' "$1" | grep -q "대상 런이 없습니다"; }

# 반사실 스크립트 — '→ A - B : alpha ...' 한 줄이 결론이다
run() {
  local label="$1"; shift
  local out delta verify guard
  out=$("$PY" "$@" 2>&1)
  save "$label" "$out"
  if noruns "$out"; then
    printf '%-30s (대상 런 없음 — 아직 평가가 성숙하지 않았다)\n' "$label"
    return
  fi
  verify=$(printf '%s\n' "$out" | grep -E "자기검증" | head -1 | sed 's/^ *//')
  delta=$(printf '%s\n'  "$out" | grep -E "→ .* - .* :" | head -1 | sed 's/^ *//')
  guard=$(printf '%s\n'  "$out" | grep -cE "자기검증 실패|자기검증 불가|믿지 말 것")
  printf '%-30s %s\n' "$label" "${delta:-(델타 없음 — 원문 확인)}"
  printf '%-30s %s\n' "" "${verify:-(자기검증 줄 없음)}"
  [ "$guard" -gt 0 ] && printf '%-30s ⚠️  이 줄은 쓰지 말 것\n' ""
}

# 스윕 — 표가 결론이라 통째로 보여준다(8줄). 억지로 한 줄로 줄이면 판단을 못 한다.
run_sweep() {
  local label="$1"; shift
  local out
  out=$("$PY" "$@" 2>&1)
  save "$label" "$out"
  printf '\n[%s]\n' "$label"
  if noruns "$out"; then
    printf '  (대상 런 없음 — 아직 평가가 성숙하지 않았다)\n'
    return
  fi
  printf '%s\n' "$out" | grep -E "자기검증|스코어러 arm|%p|모/가/감|믿지 말 것" | sed 's/^/  /'
}

echo "############ 1. KR 임계값 — 사이즈 대조 필수 ############"
echo "  1단계만 보면 '임계값↑ → 알파↓' 로 깔끔하지만, 거래대금 분위 안에서 다시 재야 한다."
echo "  (2026-08-10: KR >=70 의 -0.64% 는 대부분 Q5 쏠림이었다)"
hr
psql "$DATABASE_URL" -v mkt="'KR'" -v days=90 -f scripts/threshold_size_control.sql
hr
echo
echo "  US 도 같이 — 두 시장이 같은 방향인지 본다"
hr
psql "$DATABASE_URL" -v mkt="'US'" -v days=90 -f scripts/threshold_size_control.sql
hr

echo
echo "############ 2. 가치 가중치 상향 (핵심 안건) ############"
echo "  모집단 고정(--require-value)이 기본이다. 대조군은 --no-require-value."
echo "  구간: $VAL_FROM ~ (가치 피처가 full 로 실린 뒤)"
hr
run_sweep "스윕 KR 모집단고정" scripts/sweep_weights.py --market KR --scorer v21 --from "$VAL_FROM" --to "$VAL_TO"
run_sweep "스윕 KR 대조군" scripts/sweep_weights.py --market KR --scorer v21 --from "$VAL_FROM" --to "$VAL_TO" --no-require-value
echo
echo "  3단계 — 기간 이분 (각 5~6런. 얇으면 결론을 미룬다)"
run_sweep "스윕 KR 전반" scripts/sweep_weights.py --market KR --scorer v21 --from "$VAL_FROM" --to "$SPLIT"
run_sweep "스윕 KR 후반" scripts/sweep_weights.py --market KR --scorer v21 --from "$SPLIT" --to "$VAL_TO"
hr

echo
echo "############ 3. 가중치를 올리면 검증된 신호가 살아나는가 ############"
echo "  성장률·부채비율을 현행 가중치와 상향 가중치에서 각각 잰다."
hr
run "성장률 KR 현행가중" scripts/counterfactual_growth.py --market KR --scorer v21 --from "$VAL_FROM" --to "$VAL_TO"
run "성장률 KR 품질반영" scripts/counterfactual_growth.py --market KR --scorer v21 --from "$VAL_FROM" --to "$VAL_TO" --count-quality
run "부채비율 KR 현행"   scripts/counterfactual_debt.py   --market KR --scorer v21 --from "$VAL_FROM" --to "$VAL_TO"
run "부채비율 KR 품질반영" scripts/counterfactual_debt.py --market KR --scorer v21 --from "$VAL_FROM" --to "$VAL_TO" --count-quality
hr

cat <<'NOTE'

판단 기준
  1) 효과      — Δ알파가 양수인가
  2) 아티팩트  — 스윕은 --require-value 로 모집단을 고정한 값과 대조군을 **함께** 본다.
                 둘이 크게 다르면 모집단 효과다(US 가중치가 이렇게 무너졌다).
  3) 재현      — 전반/후반 부호가 같은가. 갈리면 기각한다.

⚠️ 표본 주의
  가치 피처는 08-11 부터라, 9/1 이면 성숙한 런이 **약 11개**(이분하면 각 5~6개)다.
  8/20 안건을 미룬 것과 같은 수준의 얇기다. 3단계가 애매하면 **결론을 9/15 로 미루고**
  런이 20개쯤 쌓인 뒤 다시 본다 — 억지로 배포 결정을 내리지 말 것.

⚠️ 자기검증
  '자기검증 불가(0건)' 은 재현 실패가 아니라 **모집단 필터가 전부 걸러낸 것**이다.
  가치 스윕을 08-11 이전 구간에 돌리면 그렇게 나온다.

원문은 /tmp/v0901_*.txt 에 저장했다.
NOTE
