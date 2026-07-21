"""当日の馬場バイアス(前残り/差し有利)の推定と脚質評価への補正。

各レースの「前残り度」= 4角で前にいたグループと後ろにいたグループの
着順率の差(-1〜+1)。同じ日・競馬場・馬場の先行レースの平均から
「今日は前が残る/差しが決まる」を推定し、脚質評価に反映する。

前有利は競馬の恒常的な傾向なので、補正には全体平均(BASE_BIAS)からの
乖離だけを使う。
"""

from __future__ import annotations

from .models import HorseEntry
from .running_style import infer_style

# 4角前1/3 と後1/3 の着順率差の全体平均(2022-2026中央13,481レースで実測)
BASE_BIAS = 0.368

# 脚質ごとの補正方向(前有利の日に有利なら正)
STYLE_DIRECTION = {"逃げ": 1.0, "先行": 0.5, "差し": -0.5, "追込": -1.0}

# バイアス乖離1.0あたり脚質スコア(0-100)に加えるポイント
BIAS_POINTS = 100.0


def race_front_bias(race: dict) -> float | None:
    """1レースの前残り度。4角通過が無い・少頭数のレースは None。"""
    horses = race.get("horses", [])
    n_field = len(horses)
    runs = []
    for h in horses:
        res = h.get("result") or {}
        pos, fin = res.get("position_4c"), res.get("finish_position")
        if pos and fin:
            runs.append((pos, fin))
    n = len(runs)
    if n < 6 or n_field < 6:
        return None
    runs.sort()
    third = max(2, n // 3)

    def perf(fin: int) -> float:
        return 1.0 - (fin - 1) / (n_field - 1)

    front = sum(perf(f) for _, f in runs[:third]) / third
    back = sum(perf(f) for _, f in runs[-third:]) / third
    return front - back


def style_bias_adjustment(
    horse: HorseEntry, bias: float, points: float = BIAS_POINTS
) -> float:
    """推定バイアス(BASE_BIASからの乖離)による脚質スコア補正値。"""
    style, _ = infer_style(horse)
    direction = STYLE_DIRECTION.get(style or "", 0.0)
    return points * bias * direction
