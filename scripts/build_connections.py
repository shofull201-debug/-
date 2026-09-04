"""データセットから騎手・調教師の複勝統計を構築する。

使い方:
    python scripts/build_connections.py data/dataset_2022_2026_v3.json.gz
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.scrape.dataset import load_dataset  # noqa: E402

MIN_RIDES = 10  # これ未満は事前値と実質同じなので収録しない


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("datasets", nargs="+")
    ap.add_argument("-o", "--output", default="src/keiba/data/connections.json")
    args = ap.parse_args()

    stats = {"jockeys": defaultdict(lambda: [0, 0]), "trainers": defaultdict(lambda: [0, 0])}
    for path in args.datasets:
        for race in load_dataset(path)["races"]:
            for h in race["horses"]:
                fin = h["result"]["finish_position"]
                place = 1 if (fin or 99) <= 3 else 0
                for key, name in (("jockeys", h.get("jockey")),
                                  ("trainers", h.get("trainer"))):
                    if name:
                        stats[key][name][0] += place
                        stats[key][name][1] += 1

    out = {
        key: {name: v for name, v in sorted(table.items()) if v[1] >= MIN_RIDES}
        for key, table in stats.items()
    }
    out["_comment"] = "騎手・調教師の[3着内数, 騎乗数]。build_connections.py で構築"
    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"騎手 {len(out['jockeys'])} 人 / 調教師 {len(out['trainers'])} 人 → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
