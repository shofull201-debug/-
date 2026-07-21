"""総合予想エンジン。

3 要素（西田式スピード指数・血統・追切）をそれぞれ算出し、出走メンバー内の
偏差値に変換してから重み付き合成で総合評価を出す。

偏差値化する理由: スピード指数（〜110 程度）と血統・追切スコア（0〜100）は
スケールも分散も異なるため、生値のまま足すと配分が崩れる。メンバー内での
相対的な位置に揃えることで、重みが意図どおり効く。

欠損の扱い: データが取れなかった要素（追切なし・血統未登録・過去走なし等）は
中立値で評価に混ぜず、その馬についてはその要素の重みを残りの要素へ再配分する。
「情報が無い」ことと「評価が低い」ことを区別するため。偏差値の母集団も
データがある馬だけで計算する（欠損馬の中立値が分布を歪めないように）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from .connections import connections_score
from .going_aptitude import going_aptitude_score, is_wet
from .models import HorseEntry, RaceCard
from .pedigree import pedigree_score
from .running_style import style_fit_score
from .speed_index import aggregate_speed_score
from .workout import workout_score

# デフォルトの重み（スピード指数を主軸に、追切・血統・脚質・騎手厩舎で補正）
# 追切0.2は坂路好タイム1年分のバックテスト(3,063レース)で0.1/0.3より
# 回収率・的中率のバランスが良かった値
DEFAULT_WEIGHTS = {
    "speed": 0.5, "workout": 0.2, "pedigree": 0.2, "style": 0.1,
    "connections": 0.1,
}

# 当日馬場が良以外のとき、道悪適性を第4の要素として自動追加する重み
# （他の重みと合算後に正規化されるため、渋るほど道悪適性の比重が上がる）
WET_FACTOR_WEIGHTS = {"稍重": 0.15, "重": 0.25, "不良": 0.30}

# 上位馬に付ける印
MARKS = ("◎", "○", "▲", "△", "△")


@dataclass
class HorseResult:
    """1 頭分の評価結果。"""

    name: str
    horse_number: int | None
    total: float                       # 総合点（偏差値の加重合成）
    mark: str                          # 印（◎○▲△、無印は ""）
    rank: int
    speed_score: float                 # 過去5走の集約スピード指数
    speed_indices: list[float]         # 各走の生指数（直近順）
    pedigree: dict                     # 血統スコア内訳
    workout: dict                      # 追切スコア内訳
    style: dict | None = None          # 脚質×コース形態の内訳
    going_aptitude: dict | None = None  # 道悪適性の内訳（良馬場のときは None）
    connections: dict | None = None    # 騎手・調教師の内訳
    # 各要素の偏差値。欠損（データなし・重みは他要素へ再配分）は None
    deviations: dict[str, float | None] = field(default_factory=dict)


def _to_deviation(values: list[float]) -> list[float]:
    """メンバー内偏差値（平均 50, 標準偏差 10）へ変換する。"""
    if len(values) <= 1:
        return [50.0 for _ in values]
    mu = mean(values)
    sigma = pstdev(values)
    if sigma == 0:
        return [50.0 for _ in values]
    return [50.0 + (v - mu) / sigma * 10 for v in values]


def _subset_deviation(
    values: list[float], present: list[bool]
) -> list[float | None]:
    """データがある馬だけを母集団として偏差値化し、欠損馬は None を返す。

    母集団が出走頭数より少ないときは √(データあり頭数/出走頭数) で
    50 へ縮小する。数頭だけの母集団で計算した偏差値は振れが大きく、
    そのまま使うと少数派どうしの比較が総合点を過剰に動かすため。
    """
    n_present = sum(present)
    shrink = (n_present / len(present)) ** 0.5 if present else 1.0
    devs = iter(
        50.0 + (d - 50.0) * shrink
        for d in _to_deviation([v for v, p in zip(values, present) if p])
    )
    return [next(devs) if p else None for p in present]


def _missing_factors(raw: dict) -> set[str]:
    """生スコアの内訳から「データが取れなかった要素」を判定する。"""
    missing = set()
    if not raw["speed_indices"]:
        missing.add("speed")
    if raw["workout"]["latest"] is None:
        missing.add("workout")
    ped = raw["pedigree"]
    if not ped["sire_known"] and not ped["dam_sire_known"]:
        missing.add("pedigree")
    if raw["style"]["style"] is None:
        missing.add("style")
    if not raw["going_aptitude"]["known"]:
        missing.add("going")
    if not raw["connections"]["known"]:
        missing.add("connections")
    return missing


def evaluate_horse(
    horse: HorseEntry, surface: str, distance: int, course: str = ""
) -> dict:
    """1 頭の各要素の生スコアを算出する。"""
    speed, indices = aggregate_speed_score(horse.past_races, surface, distance)
    ped = pedigree_score(horse.sire, horse.dam_sire, surface, distance)
    work = workout_score(horse.workouts)
    return {
        "speed": speed,
        "speed_indices": indices,
        "pedigree": ped,
        "workout": work,
        "style": style_fit_score(horse, course, surface, distance),
        "going_aptitude": going_aptitude_score(horse),
        "connections": connections_score(horse.jockey, horse.trainer),
    }


def predict(card: RaceCard, weights: dict[str, float] | None = None) -> list[HorseResult]:
    """レースカード全頭を評価し、総合点順のランキングを返す。"""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    # 道悪なら道悪適性を第4の要素として追加（明示指定があればそちらを優先）
    race = card.race
    wet = is_wet(race.going)
    if wet:
        w.setdefault("going", WET_FACTOR_WEIGHTS[race.going])
    else:
        w["going"] = 0.0

    total_w = sum(w.values())
    w = {k: v / total_w for k, v in w.items()}

    raws = [
        evaluate_horse(h, race.surface, race.distance, course=race.course)
        for h in card.horses
    ]
    missing = [_missing_factors(r) for r in raws]

    # 偏差値の母集団はデータがある馬だけ（欠損馬の中立値で分布を歪めない）
    def devs(key: str, values: list[float]) -> list[float | None]:
        return _subset_deviation(values, [key not in m for m in missing])

    dev = {
        "speed": devs("speed", [r["speed"] for r in raws]),
        "pedigree": devs("pedigree", [r["pedigree"]["score"] for r in raws]),
        "workout": devs("workout", [r["workout"]["score"] for r in raws]),
        "going": devs("going", [r["going_aptitude"]["score"] for r in raws]),
        "style": devs("style", [r["style"]["score"] for r in raws]),
        "connections": devs("connections", [r["connections"]["score"] for r in raws]),
    }

    shown = (["speed", "workout", "pedigree", "style", "connections"]
             + (["going"] if wet else []))
    results = []
    for i, (horse, raw) in enumerate(zip(card.horses, raws)):
        # 欠損要素の重みを、その馬が持っている要素へ比例配分する
        avail = {k: w.get(k, 0.0) for k in dev if k not in missing[i]}
        avail_total = sum(avail.values())
        if avail_total > 0:
            total = sum(dev[k][i] * v / avail_total for k, v in avail.items())
        else:
            total = 50.0  # 全要素欠損（実質あり得ない）は中立
        deviations = {
            k: round(dev[k][i], 1) if dev[k][i] is not None else None
            for k in shown
        }
        results.append(
            HorseResult(
                name=horse.name,
                horse_number=horse.horse_number,
                total=round(total, 2),
                mark="",
                rank=0,
                speed_score=round(raw["speed"], 1),
                speed_indices=[round(v, 1) for v in raw["speed_indices"]],
                pedigree=raw["pedigree"],
                workout=raw["workout"],
                style=raw["style"],
                going_aptitude=raw["going_aptitude"] if wet else None,
                connections=raw["connections"],
                deviations=deviations,
            )
        )

    results.sort(key=lambda r: r.total, reverse=True)
    for i, r in enumerate(results):
        r.rank = i + 1
        r.mark = MARKS[i] if i < len(MARKS) else ""
    return results
