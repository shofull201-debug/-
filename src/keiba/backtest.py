"""バックテストと重み最適化。

データセット（scrape.dataset の形式）に対して予想を回し、
◎の勝率・複勝率・単勝回収率などを計測する。

重み探索を高速化するため、重みに依存しない「各要素の偏差値」を
レースごとに前計算しておき、重みの組み合わせごとの評価は
加重和の並べ替えだけで済ませる。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev

from .going_aptitude import is_wet
from .models import HorseEntry
from .predictor import _to_deviation, evaluate_horse

FACTORS = ("speed", "workout", "pedigree", "going")


@dataclass
class PrecompRace:
    """1 レース分の前計算結果。"""

    deviations: list[dict[str, float]]   # 馬ごとの {speed, workout, pedigree} 偏差値
    finish: list[int | None]             # 実際の着順
    odds: list[float | None]             # 単勝オッズ


def precompute(dataset: dict, variants=None) -> list[PrecompRace]:
    """データセット全レースの偏差値・結果を前計算する。

    variants に track_variant.VariantTable を渡すと、各出走馬の過去走に
    同日レースから算出した馬場指数を適用してからスピード指数を計算する。
    """
    out: list[PrecompRace] = []
    for race_data in dataset["races"]:
        info = race_data["race"]
        horses = [HorseEntry.from_dict(h) for h in race_data["horses"]]
        if variants is not None:
            variants.apply_to_horses(horses)
        raws = [evaluate_horse(h, info["surface"], info["distance"]) for h in horses]

        dev_speed = _to_deviation([r["speed"] for r in raws])
        dev_ped = _to_deviation([r["pedigree"]["score"] for r in raws])
        dev_work = _to_deviation([r["workout"]["score"] for r in raws])
        # 道悪適性は当日が良以外のレースでのみ差別化要素になる（良なら全馬50=中立）
        if is_wet(info.get("going", "良")):
            dev_going = _to_deviation([r["going_aptitude"]["score"] for r in raws])
        else:
            dev_going = [50.0] * len(raws)

        finish = [h.get("result", {}).get("finish_position") for h in race_data["horses"]]
        odds = [h.get("result", {}).get("odds") for h in race_data["horses"]]

        out.append(
            PrecompRace(
                deviations=[
                    {"speed": s, "pedigree": p, "workout": w, "going": g}
                    for s, p, w, g in zip(dev_speed, dev_ped, dev_work, dev_going)
                ],
                finish=finish,
                odds=odds,
            )
        )
    return out


def active_factors(precomp: list[PrecompRace]) -> list[str]:
    """データセット内で分散を持つ（＝最適化の意味がある）要素を返す。

    例えばスクレイピングデータには追切が無く全馬 50 点になるため、
    workout は最適化対象から外す。
    """
    result = []
    for factor in FACTORS:
        values = [d[factor] for race in precomp for d in race.deviations]
        if len(values) > 1 and pstdev(values) > 1e-9:
            result.append(factor)
    return result


def evaluate_weights(precomp: list[PrecompRace], weights: dict[str, float]) -> dict:
    """指定重みでの成績を計測する。

    - win_rate:   ◎（総合 1 位）が 1 着になった率
    - place_rate: ◎が 3 着以内に入った率
    - roi:        ◎に単勝 100 円を賭け続けた場合の回収率（%）
    - top3_hit:   印上位 3 頭のうち 3 着以内に入った頭数の平均
    """
    n = wins = places = 0
    returns = 0.0
    top3_hits = 0

    for race in precomp:
        scored = [
            (sum(dev[f] * weights.get(f, 0.0) for f in FACTORS), i)
            for i, dev in enumerate(race.deviations)
            if race.finish[i] is not None  # 取消・除外は評価対象外
        ]
        if len(scored) < 3:
            continue
        scored.sort(reverse=True)
        n += 1

        honmei = scored[0][1]
        if race.finish[honmei] == 1:
            wins += 1
            if race.odds[honmei]:
                returns += race.odds[honmei] * 100
        if race.finish[honmei] <= 3:
            places += 1
        top3_hits += sum(1 for _, i in scored[:3] if race.finish[i] <= 3)

    if n == 0:
        return {"races": 0, "win_rate": 0.0, "place_rate": 0.0, "roi": 0.0, "top3_hit": 0.0}
    return {
        "races": n,
        "win_rate": round(wins / n, 4),
        "place_rate": round(places / n, 4),
        "roi": round(returns / (n * 100) * 100, 1),
        "top3_hit": round(top3_hits / n, 3),
    }


def grid_search(
    precomp: list[PrecompRace],
    step: float = 0.05,
    objective: str = "place_rate",
    factors: list[str] | None = None,
) -> list[dict]:
    """重みのグリッドサーチ。objective の降順で全候補を返す。

    factors を指定しなければ、分散のある要素だけを探索し、
    それ以外の重みは 0 に固定する。
    """
    if factors is None:
        factors = active_factors(precomp)
    if not factors:
        raise ValueError("最適化できる要素がありません（全要素が一定値です）")

    steps = round(1 / step)
    results = []
    seen: set[tuple] = set()

    def combos(remaining: list[str], budget: int, acc: dict[str, int]):
        if len(remaining) == 1:
            yield {**acc, remaining[0]: budget}
            return
        for v in range(budget + 1):
            yield from combos(remaining[1:], budget - v, {**acc, remaining[0]: v})

    for combo in combos(list(factors), steps, {}):
        weights = {f: combo.get(f, 0) * step for f in FACTORS}
        key = tuple(round(weights[f], 4) for f in FACTORS)
        if key in seen:
            continue
        seen.add(key)
        metrics = evaluate_weights(precomp, weights)
        results.append({"weights": {f: round(weights[f], 3) for f in FACTORS}, **metrics})

    results.sort(key=lambda r: (r[objective], r["roi"]), reverse=True)
    return results
