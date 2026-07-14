"""追切評価。

最終追い切りを中心に、時計（全体タイム・終い 1F）、追い方（馬なり/強め/一杯）、
併せ馬の結果を加味して 0〜100 で採点する。
評価基準は data/workout_standards.json に持ち、調整可能。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from .models import Workout

# 最終追い切りとそれ以前のベストのブレンド比率
LATEST_WEIGHT = 0.7

# 4段階評価に対応する点数
GRADE_POINTS = {"excellent": 90.0, "good": 75.0, "average": 60.0, "slow": 40.0}

# コース追いの序盤1Fあたりの想定タイム（秒）。計測ハロン数が基準表と異なる場合、
# 序盤の流し分をこのペースで加減算して基準のハロン数に換算する。
# （実際の6F追いはラスト重視で出だしが16秒台のため、線形スケールでは
#  4F・5F計時の追い切りを過大評価してしまう）
WARMUP_PACE = 16.5


@lru_cache(maxsize=1)
def _load_standards() -> dict:
    with resources.files("keiba.data").joinpath("workout_standards.json").open(
        encoding="utf-8"
    ) as f:
        return json.load(f)


def _time_to_points(time_sec: float, thresholds: dict[str, float]) -> float:
    """タイムを基準表と比較し、4 段階の間を線形補間して点数化する（20〜100）。"""
    ex, gd, av, sl = (
        thresholds["excellent"],
        thresholds["good"],
        thresholds["average"],
        thresholds["slow"],
    )
    if time_sec <= ex:
        # excellent より速い分は 0.1 秒 = 1 点で加点（上限 100）
        return min(100.0, GRADE_POINTS["excellent"] + (ex - time_sec) * 10)
    for lo_key, hi_key in (("excellent", "good"), ("good", "average"), ("average", "slow")):
        lo_t, hi_t = thresholds[lo_key], thresholds[hi_key]
        if time_sec <= hi_t:
            ratio = (time_sec - lo_t) / (hi_t - lo_t)
            return GRADE_POINTS[lo_key] + (GRADE_POINTS[hi_key] - GRADE_POINTS[lo_key]) * ratio
    # slow より遅い分は 0.1 秒 = 1 点で減点（下限 20）
    return max(20.0, GRADE_POINTS["slow"] - (time_sec - sl) * 10)


def _find_standard(facility: str, course: str) -> dict | None:
    std = _load_standards()["standards"]
    return std.get(f"{facility}|{course}") or std.get(f"その他|{course}")


def score_single_workout(w: Workout) -> float:
    """追切 1 本を 0〜100 で採点する。基準が無いコースは中立の 50 点。"""
    standard = _find_standard(w.facility, w.course)
    if standard is None:
        return 50.0

    # 計測ハロン数が基準と違う場合は、差分を序盤の流しペースで加減算して換算する
    total = w.total_time
    if w.furlongs != standard["furlongs"] and w.furlongs > 0:
        total = w.total_time + (standard["furlongs"] - w.furlongs) * WARMUP_PACE

    time_pts = _time_to_points(total, standard["total"])
    last1f_pts = _time_to_points(w.last_1f, standard["last_1f"])
    score = time_pts * 0.6 + last1f_pts * 0.4

    cfg = _load_standards()
    score += cfg["intensity_bonus"].get(w.intensity, 0)
    if w.partner_result:
        score += cfg["partner_bonus"].get(w.partner_result, 0)

    return max(0.0, min(100.0, score))


def workout_score(workouts: list[Workout]) -> dict:
    """追切一覧（新しい順）から総合スコアを算出する。

    最終追い切り（直近 1 本）を LATEST_WEIGHT、それ以前のベストを残りの比率で
    ブレンドする。追切データが無い場合は中立の 50 点。
    """
    if not workouts:
        return {"score": 50.0, "latest": None, "best_other": None}

    latest = score_single_workout(workouts[0])
    others = [score_single_workout(w) for w in workouts[1:]]
    if others:
        best_other = max(others)
        total = latest * LATEST_WEIGHT + best_other * (1 - LATEST_WEIGHT)
    else:
        best_other = None
        total = latest

    return {
        "score": round(total, 1),
        "latest": round(latest, 1),
        "best_other": round(best_other, 1) if best_other is not None else None,
    }
