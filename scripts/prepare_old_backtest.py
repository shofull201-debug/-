"""旧データセット(keiba_list変換、2022-2025)をバックテスト可能な形に整える。

やること:
1. 血統マップ(kettou2由来)で sire / dam_sire を補完(現役登録馬のみ判明)
2. データセット内の時系列から各馬の past_races(直近5走)を組み立てる

制約(seseki2系との違い):
- 配当・オッズが無い → 回収率は計算不可、的中率のみ
- 着順は同レース内のタイム順の近似(同タイムの前後は不正確)
- 通過順位が無い → 脚質評価は不発(中立)
- 血統はマップに載っている馬(現役中心)のみ

使い方:
    python scripts/prepare_old_backtest.py data/dataset_2022_2025.json.gz \
        -o data/dataset_2022_2025_bt.json.gz
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.scrape.dataset import load_dataset, save_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset")
    ap.add_argument("-o", "--output", default="data/dataset_2022_2025_bt.json.gz")
    ap.add_argument("--sire-map", default="data/sire_map.json.gz")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    races = sorted(ds["races"], key=lambda r: r["race"]["date"])

    maps = load_dataset(args.sire_map)
    sire_map, dam_map = maps["sire_map"], maps["dam_sire_map"]

    filled = total = 0
    history: dict[str, list[dict]] = defaultdict(list)
    for race in races:
        info = race["race"]
        for h in race["horses"]:
            total += 1
            if not h.get("sire"):
                h["sire"] = sire_map.get(h["name"], "")
                h["dam_sire"] = dam_map.get(h["name"])
                filled += bool(h["sire"])
            h["past_races"] = list(reversed(history[h["name"]][-5:]))
            history[h["name"]].append({
                "date": info["date"], "course": info["course"],
                "surface": info["surface"], "distance": info["distance"],
                "going": info["going"], "time_sec": h["result"]["time_sec"],
                "weight_carried": h["weight_carried"],
                "finish_position": h["result"]["finish_position"],
                "field_size": len(race["horses"]),
                "race_class": info["race_class"],
                "position_4c": None,
            })

    print(f"血統補完: {filled}/{total} 走 ({filled/total*100:.1f}%)")
    save_dataset({"races": races}, args.output)
    print(f"{len(races)} レースを {args.output} に保存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
