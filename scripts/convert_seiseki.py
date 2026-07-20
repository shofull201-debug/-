"""TARGET成績出力(seiseki.csv)を取り込み、レースカードへ通過順位などを補完する。

seiseki.csv の形式(cp932、ヘッダあり):
    データ順番号,日付,曜日,開催,Ｒ,レース名,...,着順,距離,馬場状態,着差,
    2角,3角,4角,上り3F,上3F地点差,馬体重,馬体重増減,...,種牡馬,母父馬
- 日付は YYMMDD(例 260712 → 2026-07-12)
- 着順は全角数字。中止(止)・除外(外)・消は取り込まない
- 走破タイムは含まれないため、タイムは既存データ(keiba_list等)を使う

やること:
1. (馬名, 日付) → {4角通過順位, 上がり3F, 馬体重, 増減} の索引を
   data/seiseki_index.json.gz に保存
2. --apply で指定したレースカードの past_races に position_4c を補完
   (脚質×コース形態の評価が自動で効くようになる)

使い方:
    python scripts/convert_seiseki.py seiseki.csv --apply data/*.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.scrape.dataset import save_dataset  # noqa: E402

ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def parse_date(yymmdd: str) -> str:
    """YYMMDD → ISO 形式(2000年代とみなす)。"""
    s = yymmdd.strip()
    return f"20{s[0:2]}-{s[2:4]}-{s[4:6]}"


def to_int(value: str | None) -> int | None:
    s = (value or "").strip().translate(ZEN2HAN)
    return int(s) if s.lstrip("+-").isdigit() else None


def to_float(value: str | None) -> float | None:
    s = (value or "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def build_index(path: str) -> dict[str, dict]:
    """(馬名|日付) → 補完情報 の索引を作る。"""
    index: dict[str, dict] = {}
    with open(path, encoding="cp932", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("馬名S") or "").strip()
            if not name or to_int(row.get("着順")) is None:
                continue  # 中止・除外などは評価対象外
            entry = {
                "position_4c": to_int(row.get("4角")) or to_int(row.get("3角")),
                "last_3f": to_float(row.get("上り3F")),
                "margin": to_float(row.get("着差")),
                "body_weight": to_int(row.get("馬体重")),
                "weight_diff": to_int(row.get("馬体重増減")),
            }
            index[f"{name}|{parse_date(row['日付'])}"] = {
                k: v for k, v in entry.items() if v is not None
            }
    return index


def apply_to_card(card_path: str, index: dict[str, dict]) -> int:
    """カードの past_races に position_4c を補完する(既存値は触らない)。"""
    card = json.loads(Path(card_path).read_text(encoding="utf-8"))
    applied = 0
    for horse in card.get("horses", []):
        for pr in horse.get("past_races", []):
            if pr.get("position_4c") is not None:
                continue
            info = index.get(f"{horse['name']}|{pr['date']}")
            if info and "position_4c" in info:
                pr["position_4c"] = info["position_4c"]
                applied += 1
    if applied:
        Path(card_path).write_text(
            json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return applied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("seiseki", help="TARGET成績CSV")
    ap.add_argument("-o", "--output", default="data/seiseki_index.json.gz")
    ap.add_argument("--apply", nargs="*", default=[],
                    help="position_4c を補完するレースカードJSON")
    args = ap.parse_args()

    index = build_index(args.seiseki)
    dates = sorted(k.split("|")[1] for k in index)
    print(f"成績索引: {len(index)} 走 ({dates[0]} 〜 {dates[-1]})")
    save_dataset({"seiseki": index}, args.output)
    print(f"索引を {args.output} に保存")

    for card_path in args.apply:
        n = apply_to_card(card_path, index)
        print(f"{card_path}: position_4c を {n} 走に補完")
    return 0


if __name__ == "__main__":
    sys.exit(main())
