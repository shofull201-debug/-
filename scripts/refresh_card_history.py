"""既存のレースカードの過去走を最新データセットから引き直す。

追切(記事から手で入れたもの)・騎手・斤量などはそのまま残し、
past_races と血統(空のときのみ)だけを差し替える。
データセットを更新したあと、作成済みのカードを作り直さずに
最新の過去走を反映させるために使う。

使い方:
    python scripts/refresh_card_history.py data/shion_2026.json ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from build_card import build_histories  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cards", nargs="+")
    ap.add_argument("--dataset", default="data/dataset_2022_2026_v3.json.gz")
    ap.add_argument("--sire-map", default="data/sire_map.json.gz")
    args = ap.parse_args()

    dataset = load_dataset(args.dataset)
    maps = load_dataset(args.sire_map)
    sire_map, dam_map = maps["sire_map"], maps["dam_sire_map"]

    for path in args.cards:
        card = json.load(open(path, encoding="utf-8"))
        histories = build_histories(dataset, before=card["race"]["date"])
        changed = 0
        for horse in card["horses"]:
            runs = histories.get(horse["name"], [])
            past = runs[-5:]
            # 道悪適性の評価用に、直近5走に含まれない道悪走を最大3走足す
            wet_extra = [r for r in runs[:-5]
                         if r["going"] in ("稍重", "重", "不良")][-3:]
            past = list(reversed(wet_extra + past))
            before = len(horse.get("past_races") or [])
            dates_before = {r["date"] for r in horse.get("past_races") or []}
            horse["past_races"] = past
            if not horse.get("sire"):
                horse["sire"] = sire_map.get(horse["name"], "")
            if not horse.get("dam_sire"):
                horse["dam_sire"] = dam_map.get(horse["name"])
            added = sorted({r["date"] for r in past} - dates_before)
            if added:
                changed += 1
                print(f"  {horse['name']}: {before}走 → {len(past)}走"
                      f" (追加 {'/'.join(added)})")
        json.dump(card, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"{path}: {changed}/{len(card['horses'])} 頭の過去走を更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
