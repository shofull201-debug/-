"""出走表からレースカードJSONを自動生成する。

過去5走(通過順位・上がり込み)はデータセットから、父・母父は血統マップから
自動で引く。騎手名は連対統計(connections.json)のキーへ正規化を試みる。

エントリファイル形式(1行1頭、カンマ区切り):
    馬名,斤量,騎手,調教師
    (騎手・調教師は省略可。「未定」「不明」は空扱い)

使い方:
    python scripts/build_card.py --name 関屋記念 --date 2026-07-26 \
        --course 新潟 --surface 芝 --distance 1600 --race-class G3 \
        entries.csv -o data/sekiya_2026.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from keiba.connections import _load_default  # noqa: E402
from keiba.scrape.dataset import load_dataset  # noqa: E402


def build_histories(dataset: dict, before: str) -> dict[str, list[dict]]:
    """データセット全レースから 馬名→過去走リスト(古い順) を作る。"""
    history: dict[str, list[dict]] = defaultdict(list)
    for race in sorted(dataset["races"], key=lambda r: r["race"]["date"]):
        info = race["race"]
        if info["date"] >= before:
            continue
        agaris = [h["result"].get("last_3f") for h in race["horses"]]
        agaris = [a for a in agaris if a]
        avg_3f = sum(agaris) / len(agaris) if agaris else None
        for h in race["horses"]:
            res = h["result"]
            history[h["name"]].append({
                "date": info["date"], "course": info["course"],
                "surface": info["surface"], "distance": info["distance"],
                "going": info["going"], "time_sec": res["time_sec"],
                "weight_carried": h["weight_carried"],
                "finish_position": res["finish_position"],
                "field_size": len(race["horses"]),
                "race_class": info["race_class"],
                "position_4c": res.get("position_4c"),
                "last_3f": res.get("last_3f"),
                "last_3f_rel": (
                    round(res["last_3f"] - avg_3f, 2)
                    if res.get("last_3f") and avg_3f else None
                ),
            })
    return history


def resolve_jockey(name: str, known: set[str]) -> str:
    """「M.デムーロ」→「Ｍ．デム」(TARGET短縮形)など、統計キーへの正規化を試みる。"""
    name = unicodedata.normalize("NFKC", name.strip())
    if not name or name in ("未定", "不明"):
        return ""
    norm = {unicodedata.normalize("NFKC", k): k for k in known}
    if name in norm:
        return norm[name]
    stripped = re.sub(r"^[A-Za-z]{1,2}[.．]", "", name)
    if stripped in norm:
        return norm[stripped]
    # 短縮形(前方一致)・姓のみ表記の両方向で照合
    matches = [
        orig for nk, orig in norm.items()
        if nk.startswith(name[:4]) or name.startswith(nk)
        or nk == stripped or (len(stripped) >= 3 and nk.endswith(stripped))
    ]
    if len(matches) == 1:
        return matches[0]
    return name


def resolve_trainer(name: str, known: set[str]) -> str:
    """「中内田充正」→「中内田充」(TARGET短縮形)など、統計キーへの正規化を試みる。

    調教師名はTARGET出力で末尾が欠けることがあり、不一致だと騎手・調教師要素が
    欠損扱いになる。前方一致が1件に定まるときだけ採用する。
    """
    name = unicodedata.normalize("NFKC", name.strip())
    if not name or name in ("未定", "不明"):
        return ""
    norm = {unicodedata.normalize("NFKC", k): k for k in known}
    if name in norm:
        return norm[name]
    matches = [orig for nk, orig in norm.items()
               if nk.startswith(name) or name.startswith(nk)]
    return matches[0] if len(matches) == 1 else name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("entries", help="出走馬リスト(馬名,斤量,騎手,調教師)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--course", required=True)
    ap.add_argument("--surface", required=True, choices=["芝", "ダ"])
    ap.add_argument("--distance", type=int, required=True)
    ap.add_argument("--going", default="良")
    ap.add_argument("--race-class", default="G3")
    ap.add_argument("--dataset", default="data/dataset_2022_2026_full.json.gz")
    ap.add_argument("--sire-map", default="data/sire_map.json.gz")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    dataset = load_dataset(args.dataset)
    histories = build_histories(dataset, before=args.date)
    maps = load_dataset(args.sire_map)
    sire_map, dam_map = maps["sire_map"], maps["dam_sire_map"]
    conn = _load_default()
    jockeys = set(conn.get("jockeys", {}))
    trainers = set(conn.get("trainers", {}))

    horses = []
    for line in Path(args.entries).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        weight = float(parts[1]) if len(parts) > 1 and parts[1] else 56.0
        jockey = resolve_jockey(parts[2], jockeys) if len(parts) > 2 else ""
        trainer = parts[3] if len(parts) > 3 else ""
        if trainer in ("未定", "不明"):
            trainer = ""
        trainer = re.sub(r"[((][栗美][))]", "", trainer)
        trainer = resolve_trainer(trainer, trainers)

        runs = histories.get(name, [])
        past = runs[-5:]
        # 道悪適性の実績評価用に、直近5走に含まれない道悪走を最大3走足す
        wet_extra = [r for r in runs[:-5]
                     if r["going"] in ("稍重", "重", "不良")][-3:]
        past = list(reversed(wet_extra + past))
        if not past:
            print(f"  注意: {name} の過去走がデータセットに無い")
        if jockey and jockey not in jockeys:
            print(f"  注意: 騎手「{jockey}」は統計に未収録(中立扱い)")
        if trainer and trainer not in trainers:
            print(f"  注意: 調教師「{trainer}」は統計に未収録")
        horses.append({
            "name": name,
            "sire": sire_map.get(name, ""),
            "dam_sire": dam_map.get(name),
            "weight_carried": weight,
            "jockey": jockey or None,
            "trainer": trainer or None,
            "past_races": past,
            "workouts": [],
        })

    card = {
        "race": {
            "name": args.name, "date": args.date, "course": args.course,
            "surface": args.surface, "distance": args.distance,
            "going": args.going, "race_class": args.race_class,
        },
        "horses": horses,
    }
    Path(args.output).write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_sire = sum(1 for h in horses if h["sire"])
    print(f"{args.output}: {len(horses)} 頭 (血統判明 {n_sire} 頭)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
