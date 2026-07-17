"""keiba_list.csv (TARGET系エクスポート) を keiba のデータセット形式に変換する。

入力形式 (cp932):
    馬名,レースID(新),日付(yyyy.mm.dd),レース名,場所,距離,馬場状態,走破タイム,斤量
    ジョイフルニュース,202512280605081206,2025.12.28,ファイＨ･3勝,中山,芝1600,良,1.33.6, 54

- レースID(新) の下2桁は馬番。先頭16桁でレースをグルーピングする
- クラスはレース名から判定し、重賞一覧CSV(kdiba2形式)があればグレードで上書き
  (A=G1, B=G2, C=G3。F/G/H=障害重賞は除外)
- クラスを判定できない特別戦は除外する(基準タイム・馬場指数の精度を守るため)
- 障害戦・地方は除外

使い方:
    python scripts/convert_keiba_list.py keiba_list.csv [-g kdiba2.csv keiba.csv ...] -o dataset_2022_2025.json.gz
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.scrape.dataset import save_dataset  # noqa: E402
from keiba.scrape.netkeiba import detect_class  # noqa: E402

FULLWIDTH = str.maketrans("０１２３４５６７８９ＧＬＨＳ", "0123456789GLHS")


def parse_time(text: str) -> float | None:
    """'1.33.6' / '58.9' 形式を秒に変換する。"""
    parts = text.strip().split(".")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 10
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 10
    except ValueError:
        return None
    return None


def load_grade_map(paths: list[str]) -> dict[str, str]:
    """重賞一覧CSV(レースID,曜日,レース名,重賞回,グレード,...)からグレード対応表を作る。"""
    grade_map = {}
    conv = {"A": "G1", "B": "G2", "C": "G3"}
    for path in paths:
        raw = Path(path).read_bytes().decode("cp932")
        for row in csv.reader(io.StringIO(raw)):
            if len(row) >= 5 and row[0].isdigit():
                # レースID: YYYYMMDD+場+回+日+R (12桁) → 日付+場ベースのキーにする
                grade = conv.get(row[4].strip())
                if grade:
                    grade_map[(row[0][:8], row[2])] = grade  # (yyyymmdd, レース名)
    return grade_map


def convert(list_path: str, grade_paths: list[str], min_field: int = 5) -> dict:
    grade_map = load_grade_map(grade_paths)
    raw = Path(list_path).read_bytes().decode("cp932")
    reader = csv.reader(io.StringIO(raw))
    next(reader)  # header

    races: dict[str, dict] = {}
    skipped_class = skipped_surface = 0

    for row in reader:
        if len(row) < 9:
            continue
        name, race_id_horse, date_raw, race_name, place, dist_raw, going, time_raw, weight_raw = row[:9]
        m = re.match(r"(芝|ダ|障)(\d+)", dist_raw.strip())
        if not m or m.group(1) == "障":
            skipped_surface += 1
            continue
        time_sec = parse_time(time_raw)
        if time_sec is None:
            continue
        date = date_raw.replace(" ", "").replace(".", "-")
        parts = date.split("-")
        if len(parts) != 3:
            continue
        date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"

        # クラス判定: 重賞一覧 → レース名
        norm_name = race_name.translate(FULLWIDTH)
        cls = grade_map.get((date.replace("-", ""), race_name))
        if cls is None:
            detected = detect_class(norm_name)
            # detect_classは判定不能時に'1勝'を返すため、明示表記があるかを確認
            if re.search(r"(G[123]|Jpn[123]|3勝|2勝|1勝|未勝利|新馬|オープン|OP|\(L\)|L$|1600万|1000万|500万)", norm_name):
                cls = detected
            else:
                skipped_class += 1
                continue

        race_key = race_id_horse[:16]
        horse_number = int(race_id_horse[16:18]) if race_id_horse[16:18].isdigit() else None
        race = races.setdefault(race_key, {
            "race": {
                "race_id": race_key, "name": norm_name, "date": date, "course": place,
                "surface": m.group(1), "distance": int(m.group(2)),
                "going": going.strip() or "良", "race_class": cls,
            },
            "horses": [],
        })
        try:
            weight = float(weight_raw.strip())
        except ValueError:
            weight = 56.0
        race["horses"].append({
            "name": name.strip(), "horse_number": horse_number, "sire": "",
            "weight_carried": weight, "past_races": [], "workouts": [],
            "result": {"finish_position": None, "time_sec": time_sec, "odds": None, "popularity": None},
        })

    # 着順はタイム順で近似(同着は同順にしない簡易版)。頭数下限で足切り
    out = []
    for race in races.values():
        horses = sorted(race["horses"], key=lambda h: h["result"]["time_sec"])
        if len(horses) < min_field:
            continue
        for i, h in enumerate(horses):
            h["result"]["finish_position"] = i + 1
        race["horses"] = horses
        out.append(race)
    out.sort(key=lambda r: r["race"]["date"])

    print(f"変換: {len(out)} レース / {sum(len(r['horses']) for r in out)} 走")
    print(f"除外: クラス不明 {skipped_class} 行, 障害・不正距離 {skipped_surface} 行")
    return {"races": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("list_csv", help="keiba_list.csv (馬名,レースID,日付,...)")
    ap.add_argument("-g", "--grades", nargs="*", default=[], help="重賞一覧CSV(kdiba2形式)")
    ap.add_argument("-o", "--output", default="dataset_converted.json.gz")
    ap.add_argument("--min-field", type=int, default=5, help="採用する最小頭数")
    args = ap.parse_args()

    dataset = convert(args.list_csv, args.grades, args.min_field)
    save_dataset(dataset, args.output)
    print(f"{args.output} に保存しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
