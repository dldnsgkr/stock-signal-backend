"""
재점수화 백테스트.

recommendations.feature_snapshot_json 에는 추천 시점에 계산된 피처가 그대로 남아
있다. 그 스냅샷을 다른 가중치로 다시 채점해 순위를 매기고, 이미 산출된 미래
수익률(recommendation_results)로 평가한다.

스냅샷은 추천 시점 데이터만으로 만들어졌으므로 look-ahead 가 구조적으로 불가능하다.
반대로 이 방식으로는 '피처 계산 자체'를 바꾸는 실험은 할 수 없다 — 가중치·임계값·
선택 규칙 변경만 비교 대상이다.

가격 이력이 2026-02 부터라 실질 백테스트 구간은 추천이 시작된 2026-05-18 이후이며,
이는 실제 운영 구간과 거의 겹친다. 즉 '과거를 더 확보하는 도구'가 아니라
'같은 구간에 다른 설정을 적용해 비교하는 도구'다.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.engine.scorer import BASE_WEIGHTS, BUY_THRESHOLD, calculate_total_score

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


class RescoreRequest(BaseModel):
    market: str = "US"
    fromdate: Optional[str] = None          # YYYY-MM-DD
    todate: Optional[str] = None            # YYYY-MM-DD
    weights: Optional[dict] = None          # {"momentum":.., "value":.., "sentiment":..}
    buy_threshold: Optional[float] = None
    top_n: Optional[int] = None             # 지정 시 런당 상위 N종목, 없으면 임계값 기준
    horizon: str = "7d"                     # 7d | 30d


class _Acc:
    """선택된 종목들의 수익률 누적기."""

    def __init__(self) -> None:
        self.n = 0
        self.ret_sum = 0.0
        self.alpha_sum = 0.0
        self.alpha_n = 0
        self.hits = 0

    def add(self, ret: Optional[float], alpha: Optional[float]) -> None:
        if ret is None:
            return
        self.n += 1
        self.ret_sum += ret
        if ret > 0:
            self.hits += 1
        if alpha is not None:
            self.alpha_sum += alpha
            self.alpha_n += 1

    def summary(self) -> dict:
        if self.n == 0:
            return {"count": 0, "avgReturn": None, "avgAlpha": None, "hitRate": None}
        return {
            "count": self.n,
            "avgReturn": round(self.ret_sum / self.n, 6),
            "avgAlpha": round(self.alpha_sum / self.alpha_n, 6) if self.alpha_n else None,
            "hitRate": round(self.hits / self.n, 4),
        }


def _normalize_weights(w: Optional[dict]) -> dict:
    if not w:
        return dict(BASE_WEIGHTS)
    out = {k: float(w.get(k, BASE_WEIGHTS[k])) for k in ("momentum", "value", "sentiment")}
    total = sum(out.values())
    if total <= 0:
        return dict(BASE_WEIGHTS)
    # 합이 1이 아니어도 비율만 맞으면 되도록 정규화한다.
    return {k: v / total for k, v in out.items()}


@router.post("/rescore")
async def rescore(body: RescoreRequest, db: AsyncSession = Depends(get_db)):
    market = body.market.upper()
    if market not in ("US", "KR"):
        return JSONResponse(content={"error": "market must be US or KR"}, status_code=400)
    if body.horizon not in ("7d", "30d"):
        return JSONResponse(content={"error": "horizon must be 7d or 30d"}, status_code=400)

    weights = _normalize_weights(body.weights)
    threshold = body.buy_threshold if body.buy_threshold is not None else BUY_THRESHOLD
    ret_col = "return_7d" if body.horizon == "7d" else "return_30d"
    alpha_col = "alpha_7d" if body.horizon == "7d" else "alpha_30d"

    # 평가된 수익률이 있는 런만 대상으로 한다.
    runs_sql = text(f"""
        SELECT rr.id, rr.executed_at
        FROM recommendation_runs rr
        WHERE rr.market_code = :market
          AND (:fromdate IS NULL OR rr.executed_at >= CAST(:fromdate AS date))
          AND (:todate   IS NULL OR rr.executed_at <  CAST(:todate   AS date) + 1)
          AND EXISTS (
              SELECT 1 FROM recommendations r
              JOIN recommendation_results res ON res.recommendation_id = r.id
              WHERE r.recommendation_run_id = rr.id AND res.{ret_col} IS NOT NULL
          )
        ORDER BY rr.executed_at
    """)
    runs = (await db.execute(runs_sql, {
        "market": market, "fromdate": body.fromdate, "todate": body.todate,
    })).all()

    if not runs:
        return JSONResponse(content={
            "market": market, "horizon": body.horizon, "weights": weights,
            "runs": 0, "error": "대상 런이 없습니다 (평가 완료된 추천 없음)",
        })

    # 런 단위로 끊어 읽는다. 전체를 한 번에 올리면 analysis 서비스
    # 메모리 상한(512M)을 넘긴다.
    rows_sql = text(f"""
        SELECT r.id, r.score, r.action, r.feature_snapshot_json,
               res.{ret_col} AS ret, res.{alpha_col} AS alpha
        FROM recommendations r
        JOIN recommendation_results res ON res.recommendation_id = r.id
        WHERE r.recommendation_run_id = :run_id
          AND res.{ret_col} IS NOT NULL
    """)

    variant = _Acc()      # 새 가중치로 고른 종목
    baseline = _Acc()     # 실제 운영에서 BUY 로 고른 종목
    per_run: list[dict] = []
    scoring_errors = 0

    for run_id, executed_at in runs:
        rows = (await db.execute(rows_sql, {"run_id": run_id})).all()
        if not rows:
            continue

        rescored = []
        for rid, actual_score, action, snapshot, ret, alpha in rows:
            ret_f = float(ret) if ret is not None else None
            alpha_f = float(alpha) if alpha is not None else None

            if action == "BUY":
                baseline.add(ret_f, alpha_f)

            try:
                # raw SQL 로 읽으면 드라이버에 따라 jsonb 가 문자열로 온다.
                feat = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
                detail = calculate_total_score(feat, base_weights=weights)
                new_score = detail["total_score"]
            except Exception:
                # 스냅샷 구조가 다른 과거 데이터는 건너뛴다.
                scoring_errors += 1
                continue
            rescored.append((new_score, ret_f, alpha_f))

        if not rescored:
            continue

        if body.top_n:
            rescored.sort(key=lambda x: x[0], reverse=True)
            selected = rescored[: body.top_n]
        else:
            selected = [x for x in rescored if x[0] >= threshold]

        run_acc = _Acc()
        for _, ret_f, alpha_f in selected:
            run_acc.add(ret_f, alpha_f)
            variant.add(ret_f, alpha_f)

        per_run.append({
            "runId": run_id,
            "executedAt": executed_at.isoformat(),
            "scored": len(rescored),
            **run_acc.summary(),
        })

    if scoring_errors:
        logger.warning(f"rescore: {scoring_errors} snapshots skipped (구조 불일치)")

    return JSONResponse(content={
        "market": market,
        "horizon": body.horizon,
        "weights": weights,
        "buyThreshold": threshold if not body.top_n else None,
        "topN": body.top_n,
        "runs": len(per_run),
        "skippedSnapshots": scoring_errors,
        "variant": variant.summary(),    # 새 설정
        "baseline": baseline.summary(),  # 실제 운영 결과
        "perRun": per_run,
    })
