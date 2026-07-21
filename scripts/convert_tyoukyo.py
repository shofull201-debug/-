"""TARGET調教好タイムCSV(t2.csv)を調教索引に変換する。

入力(cp932): 場所,年月日,曜日,時刻,馬名,Ｃ,性別,年齢,収得賞金,調教師,
             Time1,Time2,Time3,Time4,Lap4,Lap3,Lap2,Lap1
- Time1〜4 は 4F/3F/2F/1F の通し時計(坂路)
- 好タイム抽出のため全馬・全本数は含まれない

出力: data/workout_index.json.gz
  {"workouts": {馬名: [[ISO日付, 施設, 4F通し, 終い1F], ...]}}

使い方:
    python scripts/convert_tyoukyo.py t2.csv -o data/workout_index.json.gz
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.scrape.dataset import save_dataset  # noqa: E402

# 坂路4Fとして妥当な範囲(計測異常の除外)
TIME_RANGE = (45.0, 75.0)
LAST1F_RANGE = (10.0, 18.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csvs", nargs="+", help="調教好タイムCSV(追加分も並べて渡せる)")
    ap.add_argument("-o", "--output", default="data/workout_index.json.gz")
    args = ap.parse_args()

    index: dict[str, list] = defaultdict(list)
    kept = skipped = 0
    for path in args.csvs:
        with open(path, encoding="cp932", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("馬名") or "").strip()
                d = (row.get("年月日") or "").strip()
                try:
                    total = float(row.get("Time1") or 0)
                    last = float(row.get("Time4") or 0)
                except ValueError:
                    skipped += 1
                    continue
                if (not name or len(d) != 8
                        or not TIME_RANGE[0] <= total <= TIME_RANGE[1]
                        or not LAST1F_RANGE[0] <= last <= LAST1F_RANGE[1]):
                    skipped += 1
                    continue
                iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
                index[name].append([iso, (row.get("場所") or "").strip(), total, last])
                kept += 1

    for works in index.values():
        works.sort()
    dates = sorted(w[0] for works in index.values() for w in works)
    print(f"取り込み {kept} 本 / 除外 {skipped} 本 / {len(index)} 頭"
          f" ({dates[0]} 〜 {dates[-1]})")
    save_dataset({"workouts": dict(index)}, args.output)
    print(f"{args.output} に保存しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
