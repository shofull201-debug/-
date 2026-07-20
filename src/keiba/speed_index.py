"""西田式スピード指数の計算。

計算式:
    指数 = (基準タイム − 走破タイム) × 距離指数 × 10
           + 馬場指数
           + (斤量 − 55) × 2
           + 80

- タイムは秒単位で扱い、0.1 秒 = 距離指数 × 1 ポイントになるよう ×10 する。
- 基準タイムは「競馬場×コース種別×距離」ごとの 1勝クラス・良馬場の標準タイム。
  クラス差は class_offsets（距離でスケール）で補正する。
- 馬場指数はその日の馬場の速さの補正値。実測値があれば PastRace.track_variant に
  入れて使い、無ければ馬場状態（良/稍重/重/不良）から概算する。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from .models import PastRace

# 距離指数: 距離ごとの 1 秒の価値を揃える係数（西田式の近似値）
DISTANCE_INDEX: dict[int, float] = {
    1000: 1.63, 1150: 1.44, 1200: 1.36, 1300: 1.26, 1400: 1.17,
    1500: 1.09, 1600: 1.02, 1700: 0.96, 1800: 0.91, 1900: 0.86,
    2000: 0.82, 2100: 0.79, 2200: 0.75, 2300: 0.72, 2400: 0.68,
    2500: 0.66, 2600: 0.63, 3000: 0.55, 3200: 0.51, 3400: 0.48,
    3600: 0.45,
}

# 馬場状態から馬場指数を概算する（実測の馬場指数が無い場合のフォールバック）
# 芝は渋るほど時計がかかる → プラス補正。ダートは湿ると速くなる → マイナス補正。
GOING_VARIANT: dict[str, dict[str, float]] = {
    "芝": {"良": 0.0, "稍重": 6.0, "重": 12.0, "不良": 18.0},
    "ダ": {"良": 0.0, "稍重": -3.0, "重": -6.0, "不良": -4.0},
}


@lru_cache(maxsize=1)
def _load_base_times() -> dict:
    with resources.files("keiba.data").joinpath("base_times.json").open(encoding="utf-8") as f:
        return json.load(f)


def distance_index(distance: int) -> float:
    """距離指数を返す。表に無い距離は近い 2 点から線形補間する。"""
    table = DISTANCE_INDEX
    if distance in table:
        return table[distance]
    keys = sorted(table)
    if distance <= keys[0]:
        return table[keys[0]]
    if distance >= keys[-1]:
        return table[keys[-1]]
    lo = max(k for k in keys if k < distance)
    hi = min(k for k in keys if k > distance)
    ratio = (distance - lo) / (hi - lo)
    return table[lo] + (table[hi] - table[lo]) * ratio


def base_time(course: str, surface: str, distance: int, race_class: str = "1勝") -> float:
    """基準タイム（秒）を返す。表に無い条件はフォールバック式で概算する。"""
    data = _load_base_times()
    key = f"{course}|{surface}|{distance}"
    base = data["base_times"].get(key)
    if base is None:
        fb = data["fallback"][surface]
        base = fb["per_1000"] + (distance - 1000) / 200 * fb["per_200"]
    # クラス補正は距離に比例させる（長距離ほどクラス差のタイム差が大きい）
    offset = data["class_offsets"].get(race_class, 0.0) * (distance / 1600)
    return base + offset


def going_variant(surface: str, going: str) -> float:
    """馬場状態から馬場指数を概算する。"""
    return GOING_VARIANT.get(surface, GOING_VARIANT["芝"]).get(going, 0.0)


def nishida_speed_index(past: PastRace) -> float:
    """過去走 1 レースの西田式スピード指数を計算する。"""
    base = base_time(past.course, past.surface, past.distance, past.race_class)
    di = distance_index(past.distance)
    variant = (
        past.track_variant
        if past.track_variant is not None
        else going_variant(past.surface, past.going)
    )
    return (
        (base - past.time_sec) * 10 * di
        + variant
        + (past.weight_carried - 55.0) * 2
        + 80.0
    )


# ---- 過去 5 走の集約 -------------------------------------------------------

# 直近から順に掛ける鮮度ウェイト
RECENCY_WEIGHTS = (1.0, 0.9, 0.8, 0.7, 0.6)


def _relevance(past: PastRace, surface: str, distance: int) -> float:
    """今回条件（コース種別・距離）への関連度ウェイト（0.3〜1.0）。"""
    w = 1.0 if past.surface == surface else 0.5
    w *= max(0.6, 1.0 - abs(distance - past.distance) / 2500)
    return max(0.3, w)


# 集約の既定値: 加重平均とベスト指数のブレンド比率、加重平均から除く大敗走の数
BLEND_BEST = 0.45
TRIM_WORST = 0


def aggregate_speed_score(
    past_races: list[PastRace],
    surface: str,
    distance: int,
    blend_best: float | None = None,
    trim_worst: int | None = None,
) -> tuple[float, list[float]]:
    """過去 5 走のスピード指数を集約して 1 頭のスピードスコアにする。

    - 各走の指数に「鮮度 × 今回条件への関連度」のウェイトを掛けた加重平均と、
      最高値（ベスト指数）を blend_best の比率でブレンドする。
    - trim_worst > 0 なら、指数が最も低い走をその数だけ加重平均から除く
      （出遅れ・不利などの大敗が平均を毀損するのを防ぐ。3走以上残る場合のみ）。
    - 戻り値: (スコア, 各走の生指数リスト[直近順])
    """
    if blend_best is None:
        blend_best = BLEND_BEST
    if trim_worst is None:
        trim_worst = TRIM_WORST
    if not past_races:
        return 0.0, []

    indices = [nishida_speed_index(r) for r in past_races[:5]]
    weights = [
        RECENCY_WEIGHTS[i] * _relevance(r, surface, distance)
        for i, r in enumerate(past_races[:5])
    ]
    pairs = list(zip(indices, weights))
    if trim_worst > 0 and len(pairs) >= trim_worst + 3:
        pairs = sorted(pairs, key=lambda p: p[0])[trim_worst:]
    wavg = sum(v * w for v, w in pairs) / sum(w for _, w in pairs)
    best = max(indices)
    score = wavg * (1 - blend_best) + best * blend_best
    return score, indices
