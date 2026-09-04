"""ウォークフォワード再監査。

参照テーブル(基準タイム・種牡馬適性・騎手調教師統計)を2024年末までの
データだけで作り直し、2025-26年を「一切の未来情報なし」で再評価する。

通常のバックテストとの違い:
- 基準タイム: 2022-2024の良馬場走のみから構築(本番は2022-2025で構築)
- 種牡馬適性: 手動調整分はそのまま、自動構築分を2024年末までの産駒成績で再構築
- 騎手・調教師: 2024年末までの複勝統計のみ
- 馬場指数: 基準タイムをas-of版に差し替えて全日程を再計算
  (過去走の日の馬場指数は予想時点で判明している情報なのでリークではない)

使い方:
    python scripts/walk_forward_audit.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import keiba.connections as conn_mod  # noqa: E402
import keiba.going_aptitude as going_mod  # noqa: E402
import keiba.pedigree as ped_mod  # noqa: E402
import keiba.speed_index as si_mod  # noqa: E402
from keiba.backtest import evaluate_weights, precompute  # noqa: E402
from keiba.going_aptitude import WET_GOINGS  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402
from keiba.track_variant import VariantTable, compute_variants  # noqa: E402

SPLIT = "2025-01-01"
MIN_SAMPLES_BASE = 20
PRIOR_RUNS, MIN_TOTAL = 30, 15
MIN_SURFACE, MIN_DIST, MIN_WET = 30, 20, 15
SHRINK_JT, BASE_PLACE, MIN_RIDES = 50, 0.25, 10


def build_asof_base_times(train_races, current):
    offsets = current["class_offsets"]
    buckets = defaultdict(list)
    for r in train_races:
        info = r["race"]
        if info["going"] != "良":
            continue
        off = offsets.get(info["race_class"], 0.0) * (info["distance"] / 1600)
        for h in r["horses"]:
            buckets[f"{info['course']}|{info['surface']}|{info['distance']}"].append(
                h["result"]["time_sec"] - off)
    return {
        "class_offsets": offsets,
        "fallback": current["fallback"],
        "base_times": {k: round(sum(v) / len(v), 1)
                       for k, v in buckets.items() if len(v) >= MIN_SAMPLES_BASE},
    }


def build_asof_sires(train_races, current):
    """手動調整エントリは維持、_auto エントリを2024年末までの産駒成績で再構築。"""
    maps = load_dataset("data/sire_map.json.gz")
    sire_map = maps["sire_map"]
    dam_map = maps["dam_sire_map"]
    hand = {k: v for k, v in current["sires"].items() if not v.get("_auto")}

    def perf_to_score(vals):
        p = (sum(vals) + 0.5 * PRIOR_RUNS) / (len(vals) + PRIOR_RUNS)
        return max(1, min(10, round(5 + (p - 0.5) * 25)))

    stats = defaultdict(lambda: defaultdict(list))
    bms = defaultdict(lambda: defaultdict(list))
    for r in train_races:
        info = r["race"]
        n = len(r["horses"])
        if n < 2:
            continue
        from keiba.pedigree import distance_category
        keys = [f"surface:{info['surface']}", f"dist:{distance_category(info['distance'])}"]
        wet = info["going"] in WET_GOINGS
        for h in r["horses"]:
            perf = 1 - (h["result"]["finish_position"] - 1) / (n - 1)
            for target, mp in ((stats, sire_map), (bms, dam_map)):
                name = mp.get(h["name"])
                if name is None:
                    continue
                s = target[name]
                s["total"].append(perf)
                for k in keys:
                    s[k].append(perf)
                if wet:
                    s["wet"].append(perf)

    def entry(s):
        overall = perf_to_score(s["total"])
        def sc(vals, m):
            return perf_to_score(vals) if len(vals) >= m else overall
        return {
            "surface": {"芝": sc(s["surface:芝"], MIN_SURFACE),
                        "ダ": sc(s["surface:ダ"], MIN_SURFACE)},
            "distance": {c: sc(s[f"dist:{c}"], MIN_DIST)
                         for c in ("短距離", "マイル", "中距離", "長距離")},
            "wet": sc(s["wet"], MIN_WET),
        }

    sires = dict(hand)
    for name, s in stats.items():
        if name not in hand and len(s["total"]) >= MIN_TOTAL:
            sires[name] = entry(s)
    covered = set(sires)
    for name, s in bms.items():
        if name not in covered and len(s["total"]) >= MIN_TOTAL:
            sires[name] = entry(s)
    return {"default": current["default"], "sires": sires}


def build_asof_connections(train_races):
    stats = {"jockeys": defaultdict(lambda: [0, 0]), "trainers": defaultdict(lambda: [0, 0])}
    for r in train_races:
        for h in r["horses"]:
            place = 1 if (h["result"]["finish_position"] or 99) <= 3 else 0
            for key, name in (("jockeys", h.get("jockey")), ("trainers", h.get("trainer"))):
                if name:
                    stats[key][name][0] += place
                    stats[key][name][1] += 1
    return {k: {n: v for n, v in t.items() if v[1] >= MIN_RIDES}
            for k, t in stats.items()}


def main() -> int:
    ds = load_dataset("data/dataset_2022_2026_v3.json.gz")
    train = [r for r in ds["races"] if r["race"]["date"] < SPLIT]
    test = [r for r in ds["races"] if r["race"]["date"] >= SPLIT]
    print(f"テーブル構築: {len(train)}レース(〜2024) / 評価: {len(test)}レース(2025-26)")

    # --- as-of テーブルを構築して差し替え ---
    cur_base = json.load(open("src/keiba/data/base_times.json", encoding="utf-8"))
    asof_base = build_asof_base_times(train, cur_base)
    print(f"基準タイム: {len(asof_base['base_times'])} 条件(本番 {len(cur_base['base_times'])})")
    si_mod._load_base_times = lambda: asof_base

    cur_sires = json.load(open("src/keiba/data/sire_aptitude.json", encoding="utf-8"))
    asof_sires = build_asof_sires(train, cur_sires)
    print(f"種牡馬適性: {len(asof_sires['sires'])} 頭(本番 {len(cur_sires['sires'])})")
    ped_mod._load_sire_data = lambda: asof_sires
    going_mod._load_sire_data = lambda: asof_sires

    asof_conn = build_asof_connections(train)
    print(f"騎手 {len(asof_conn['jockeys'])} / 調教師 {len(asof_conn['trainers'])}")
    conn_mod._load_default = lambda: asof_conn

    # 馬場指数: as-of基準タイムで全日程を再計算
    from keiba.cli import _result_rows_from_dataset
    rows = _result_rows_from_dataset(ds)
    variants = VariantTable(compute_variants(rows))
    print(f"馬場指数: {len(variants.table)} 日分を as-of 基準で再計算")

    CONFIGS = {
        "採用構成(脚質0/道悪0.10)": {"speed": .5, "pedigree": .2, "style": 0,
                                    "connections": .1, "going": .10},
        "旧構成(脚質0.1/道悪0.15)": {"speed": .5, "pedigree": .2, "style": .1,
                                    "connections": .1, "going": .15},
    }

    for agari in (6.0, 0.0):
        si_mod.AGARI_COEF = agari
        pre = precompute({"races": test}, variants)
        graded = [p for p, r in zip(pre, test) if r["race"]["race_class"] in ("G1", "G2", "G3")]
        wet = [p for p, r in zip(pre, test) if r["race"]["going"] in WET_GOINGS]
        print(f"\n===== 上がり補正 {agari:+.0f} =====")
        for sname, subset in (("全レース", pre), (f"重賞({len(graded)}R)", graded),
                              (f"道悪({len(wet)}R)", wet)):
            print(f"--- {sname} ---")
            for label, w in CONFIGS.items():
                m = evaluate_weights(subset, w)
                print(f"  {label:<22} ◎勝率 {m['win_rate']*100:5.1f}%"
                      f" / 複勝率 {m['place_rate']*100:5.1f}%"
                      f" / 単回収 {m['roi']:5.1f}% / 複回収 {m['place_roi']:5.1f}%")
    si_mod.AGARI_COEF = 6.0
    return 0


if __name__ == "__main__":
    sys.exit(main())
