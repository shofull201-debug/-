"""TARGET成績完全版(seseki2.csv, 50列)を バックテスト用データセットに変換する。

seiseki.csv(35列)との違い: 馬番・走破タイム・単複配当・オッズ・PCI を含むため、
実配当での回収率最適化(keiba optimize / fit)に使える。

入力(cp932、ヘッダあり):
    Ｍ,日付,開催,Ｒ,レース名,馬名,...,人気,着順,芝・ダ,距離,コース区分,馬場状態,
    ...,走破タイム,着差,2角,3角,4角,上り3F,...,馬体重,馬体重増減,ブリンカー,
    単勝配当,複勝配当,...
- 日付は YYMMDD。開催は「2小6」= 2回小倉6日(先頭の場略号で競馬場を判定)
- 走破タイムは「1072」= 1分07秒2 の連結数字
- 単勝配当は勝ち馬のみ払戻円、他馬は「(7.4)」= 単勝オッズ
- 着順が 止/外/消 の行は除外。クラスを判定できないレースは除外
  (重賞一覧CSVがあればグレードを優先、次いで「クラス名」列)
- TARGETの全項目出力(274列)も読める。同名列が二重に出るため最初の出現を採用し、
  「芝1600」形式の距離は馬場種別を剥がす
- このCSVには種牡馬・母父の列が無いため、kettou2.csv 由来の血統マップ
  (data/sire_map.json.gz、scripts/build_sire_aptitude.py が出力)で結合する

各出走馬の past_races は同ファイル内の過去走(直近5走、4角通過つき)から
組み立てるため、スピード指数と脚質評価がバックテストで有効になる。

使い方:
    python scripts/convert_seiseki2.py seseki2.csv -g kdiba2.csv keiba.csv \
        -o data/dataset_2025_2026.json.gz
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from convert_keiba_list import load_grade_map  # noqa: E402
from keiba.scrape.dataset import save_dataset  # noqa: E402
from keiba.scrape.netkeiba import detect_class  # noqa: E402

COURSE_ABBR = {
    "札": "札幌", "函": "函館", "福": "福島", "新": "新潟", "東": "東京",
    "中": "中山", "名": "中京", "京": "京都", "阪": "阪神", "小": "小倉",
}
GOING_ABBR = {"良": "良", "稍": "稍重", "重": "重", "不": "不良"}
CLASS_PATTERN = re.compile(
    r"(G[123]|Jpn[123]|3勝|2勝|1勝|未勝利|新馬|オープン|OP|\(L\)|L$|1600万|1000万|500万)"
)
ZEN2HAN = str.maketrans(
    "０１２３４５６７８９",
    "0123456789",
)
# TARGETの「クラス名」列 → モデル内のクラス表記
CLASS_NAME_MAP = {
    "Ｇ１": "G1", "Ｇ２": "G2", "Ｇ３": "G3",
    "ＪＧ１": "G1", "ＪＧ２": "G2", "ＪＧ３": "G3",
    "ｵｰﾌﾟﾝ": "OP", "オープン": "OP", "OP(L)": "L", "OP(Ｌ)": "L",
    "3勝": "3勝", "2勝": "2勝", "1勝": "1勝",
    "未勝利": "未勝利", "新馬": "新馬",
}
# 丸数字(降着・繰り上がりなどの表記)も通常の着順として扱う
CIRCLED = {chr(0x2460 + i): str(i + 1) for i in range(20)}        # ①〜⑳
CIRCLED.update({chr(0x3251 + i): str(i + 21) for i in range(15)})  # ㉑〜㉟


def parse_time(text: str) -> float | None:
    """'1072' → 67.2 秒。'582' → 58.2 秒(下1桁が1/10秒、次の2桁が秒、残りが分)。"""
    s = (text or "").strip()
    if not s.isdigit() or len(s) < 3:
        return None
    tenth, sec, minute = int(s[-1]), int(s[-3:-1]), int(s[:-3] or 0)
    return minute * 60 + sec + tenth / 10


def to_int(value: str | None) -> int | None:
    s = (value or "").strip().translate(ZEN2HAN)
    s = "".join(CIRCLED.get(ch, ch) for ch in s)
    return int(s) if s.lstrip("+-").isdigit() else None


def to_float(value: str | None) -> float | None:
    try:
        return float((value or "").strip())
    except ValueError:
        return None


def parse_weight(text: str) -> float:
    """'54△' などの減量記号つき斤量。"""
    m = re.search(r"[\d.]+", (text or ""))
    return float(m.group()) if m else 56.0


def parse_course(kaisai: str) -> str | None:
    """開催「2小6」「3函A2」から競馬場名を得る。"""
    for ch in kaisai:
        if ch in COURSE_ABBR:
            return COURSE_ABBR[ch]
    return None


def detect_race_class(
    race_name: str, date: str, grade_map: dict, class_name: str = ""
) -> str | None:
    """重賞一覧 → クラス名列 → レース名の表記 の順で判定する。判定不能は None。

    TARGETの全項目出力には「クラス名」列があり、レース名の略記に頼らずに
    クラスが確定する(旧来はレース名に条件が出ないレースを取りこぼしていた)。
    """
    cls = grade_map.get((date.replace("-", ""), race_name))
    if cls:
        return cls
    cls = CLASS_NAME_MAP.get((class_name or "").strip())
    if cls:
        return cls
    norm = unicodedata.normalize("NFKC", race_name)
    if CLASS_PATTERN.search(norm):
        return detect_class(norm)
    return None


def read_rows(path: str):
    """同名列を含むCSVを読む(同名は最初の出現を採用)。

    TARGETの全項目出力は「距離」「走破タイム」などを表示用と数値用で二重に
    出力する。csv.DictReader は後勝ちのため「芝1600」「1.33.6」のような
    表示用の値を拾ってしまい、全行が変換対象外になる。
    """
    with open(path, encoding="cp932", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        first: dict[str, int] = {}
        for i, col in enumerate(header):
            first.setdefault(col, i)
        for row in reader:
            yield {c: (row[i] if i < len(row) else "") for c, i in first.items()}


def load_pedigree(path: str | None) -> tuple[dict, dict]:
    if not path or not Path(path).exists():
        print("血統マップなし: sire/dam_sire は空のまま")
        return {}, {}
    from keiba.scrape.dataset import load_dataset

    maps = load_dataset(path)
    return maps.get("sire_map", {}), maps.get("dam_sire_map", {})


def convert(
    path: str, grade_paths: list[str], min_field: int = 5,
    sire_map_path: str | None = None,
) -> dict:
    grade_map = load_grade_map(grade_paths)
    sire_map, dam_sire_map = load_pedigree(sire_map_path)
    races: dict[tuple, dict] = {}
    skipped_class = skipped = 0

    for row in read_rows(path):
        name = (row.get("馬名") or "").strip()
        finish = to_int(row.get("着順"))
        time_sec = parse_time(row.get("走破タイム"))
        course = parse_course(row.get("開催") or "")
        surface = (row.get("芝・ダ") or "").strip()
        # 「芝1600」形式で出力されることがあるため馬場種別を剥がす
        dist_raw = (row.get("距離") or "").strip()
        m_dist = re.match(r"^([芝ダ障])\s*(\d+)$", dist_raw)
        if m_dist:
            surface = surface or m_dist.group(1)
            dist_raw = m_dist.group(2)
        distance = to_int(dist_raw)
        if not name or finish is None or time_sec is None or course is None \
                or surface not in ("芝", "ダ") or not distance:
            skipped += 1
            continue
        date_raw = (row.get("日付") or "").strip()
        date = f"20{date_raw[0:2]}-{date_raw[2:4]}-{date_raw[4:6]}"
        race_name = (row.get("レース名") or "").strip()
        cls = detect_race_class(race_name, date, grade_map, row.get("クラス名", ""))
        if cls is None:
            skipped_class += 1
            continue

        key = (date, row["開催"].strip(), row["Ｒ"].strip())
        race = races.setdefault(key, {
            "race": {
                "race_id": f"{date}|{row['開催'].strip()}|{row['Ｒ'].strip()}",
                "name": unicodedata.normalize("NFKC", race_name),
                "date": date, "course": course, "surface": surface,
                "distance": distance,
                "going": GOING_ABBR.get((row.get("馬場状態") or "").strip(), "良"),
                "race_class": cls,
            },
            "horses": [],
            "payouts": {"win": {}, "place": {}},
        })

        number = to_int(row.get("馬番"))
        # 単勝配当: 勝ち馬は払戻円、それ以外は "(オッズ)"
        tansho = (row.get("単勝配当") or "").strip()
        odds = None
        if tansho.startswith("("):
            try:
                odds = float(tansho.strip("()"))
            except ValueError:
                pass
        elif tansho.isdigit():
            odds = int(tansho) / 100
            if number is not None:
                race["payouts"]["win"][number] = int(tansho)
        fukusho = (row.get("複勝配当") or "").strip()
        if fukusho.isdigit() and number is not None:
            race["payouts"]["place"][number] = int(fukusho)
        # 連系配当は的中馬の行に同じ値が載る(馬連=1-2着行、3連複=1-3着行)
        for col, key in (("馬連", "quinella"), ("馬単", "exacta"),
                         ("３連複", "trio"), ("３連単", "trifecta")):
            v = (row.get(col) or "").strip().replace(",", "")
            if v.isdigit() and key not in race["payouts"]:
                race["payouts"][key] = int(v)

        race["horses"].append({
            "name": name,
            "horse_number": number,
            "sire": sire_map.get(name, ""),
            "dam_sire": dam_sire_map.get(name),
            "weight_carried": parse_weight(row.get("斤量")),
            "jockey": (row.get("騎手") or "").strip(),
            "trainer": re.sub(r"^[((][栗美地外][))]", "", (row.get("調教師") or "").strip()),
            "past_races": [],
            "workouts": [],
            "result": {
                "finish_position": finish,
                "time_sec": time_sec,
                "odds": odds,
                "popularity": to_int(row.get("人気")),
                "body_weight": to_int(row.get("馬体重")),
                "weight_diff": to_int(row.get("馬体重増減")),
                "pci": to_float(row.get("PCI")),
            },
            "_position_4c": to_int(row.get("4角")) or to_int(row.get("3角")),
            "_field_size": to_int(row.get("頭数")),
            "_last_3f": to_float(row.get("上り3F")),
        })

    # 頭数下限で足切りして日付順に
    out = [r for r in races.values() if len(r["horses"]) >= min_field]
    out.sort(key=lambda r: r["race"]["date"])
    print(f"除外: クラス不明 {skipped_class} 行, 中止・欠損 {skipped} 行")
    return out


def seed_history(history_path: str, before: str) -> dict[str, list[dict]]:
    """別データセット(keiba_list変換など)から before より前の走を馬ごとに集める。

    データ期間の序盤はファイル内に過去走が無く履歴が浅くなるため、
    旧年データで接ぎ木する。通過順位(position_4c)は旧データに無いので None。
    """
    from keiba.scrape.dataset import load_dataset

    history: dict[str, list[dict]] = defaultdict(list)
    ds = load_dataset(history_path)
    for race in ds["races"]:
        info = race["race"]
        if info["date"] >= before:
            continue
        for h in race["horses"]:
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
    for runs in history.values():
        runs.sort(key=lambda r: r["date"])
    return history


def assemble_past_races(out: list[dict], seeded: dict[str, list[dict]]) -> None:
    # 各馬の past_races を時系列で組み立てる(直近5走、新しい順)
    history: dict[str, list[dict]] = defaultdict(list, seeded)
    for race in out:
        info = race["race"]
        agaris = [h["_last_3f"] for h in race["horses"] if h["_last_3f"]]
        avg_3f = sum(agaris) / len(agaris) if agaris else None
        for h in race["horses"]:
            h["past_races"] = list(reversed(history[h["name"]][-5:]))
            # 当日バイアス推定などで使えるよう、当該走の通過・上がりは結果にも残す
            h["result"]["position_4c"] = h["_position_4c"]
            h["result"]["last_3f"] = h["_last_3f"]
            history[h["name"]].append({
                "date": info["date"], "course": info["course"],
                "surface": info["surface"], "distance": info["distance"],
                "going": info["going"], "time_sec": h["result"]["time_sec"],
                "weight_carried": h["weight_carried"],
                "finish_position": h["result"]["finish_position"],
                "field_size": h["_field_size"] or len(race["horses"]),
                "race_class": info["race_class"],
                "position_4c": h["_position_4c"],
                "last_3f": h["_last_3f"],
                "last_3f_rel": (
                    round(h["_last_3f"] - avg_3f, 2)
                    if h["_last_3f"] and avg_3f else None
                ),
            })
    for race in out:
        for h in race["horses"]:
            del h["_position_4c"], h["_field_size"], h["_last_3f"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("seiseki2", help="TARGET成績完全版CSV(50列)")
    ap.add_argument("-g", "--grades", nargs="*", default=[], help="重賞一覧CSV(kdiba2形式)")
    ap.add_argument("-o", "--output", default="data/dataset_2025_2026.json.gz")
    ap.add_argument("--min-field", type=int, default=5)
    ap.add_argument("--sire-map", default="data/sire_map.json.gz",
                    help="馬名→父/母父マップ(build_sire_aptitude.py の出力)")
    ap.add_argument("--history",
                    help="旧年データセット(.json.gz)。期間序盤の past_races を接ぎ木する")
    args = ap.parse_args()

    races = convert(args.seiseki2, args.grades, args.min_field, args.sire_map)
    seeded: dict = {}
    if args.history and races:
        first_date = races[0]["race"]["date"]
        seeded = seed_history(args.history, before=first_date)
        n_runs = sum(len(v) for v in seeded.values())
        print(f"履歴の接ぎ木: {args.history} から {first_date} より前の"
              f" {len(seeded)} 頭 / {n_runs} 走")
    assemble_past_races(races, seeded)

    n_win = sum(1 for r in races if r["payouts"]["win"])
    print(f"変換: {len(races)} レース / {sum(len(r['horses']) for r in races)} 走"
          f" (単勝払戻あり {n_win} レース)")
    save_dataset({"races": races}, args.output)
    print(f"{args.output} に保存しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
