"""前向き運用の予想ログと成績集計。

バックテストと違い、発走前に出した予想を記録して結果と突き合わせるため、
データの汚染が原理的に起きない「公式成績」になる。

ログ形式(JSON配列、1要素=1レース):
{
  "race": {"name", "date", "course", "surface", "distance", "going", "race_class"},
  "picks": [{"rank", "mark", "horse_number", "name", "total"}, ...],  # 全頭・順位順
  "result": null または {
      "order": ["1着馬", "2着馬", "3着馬"],
      "win_pay": 420,                      # 勝ち馬の単勝払戻(100円あたり、任意)
      "place_pays": {"馬名": 180, ...}      # 3着内馬の複勝払戻(任意)
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path


def load_log(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save_log(path: str, entries: list[dict]) -> None:
    Path(path).write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def append_prediction(path: str, race: dict, results) -> bool:
    """予想をログへ追記する。同名・同日のエントリがあれば上書き(再予想)。"""
    entries = load_log(path)
    picks = [
        {"rank": r.rank, "mark": r.mark, "horse_number": r.horse_number,
         "name": r.name, "total": r.total}
        for r in results
    ]
    entry = {"race": race, "picks": picks, "result": None}
    for i, e in enumerate(entries):
        if e["race"]["name"] == race["name"] and e["race"]["date"] == race["date"]:
            entry["result"] = e.get("result")  # 既に結果入力済みなら保持
            entries[i] = entry
            save_log(path, entries)
            return False
    entries.append(entry)
    save_log(path, entries)
    return True


def set_result(path: str, race_name: str, order: list[str],
               win_pay: int | None = None,
               place_pays: dict[str, int] | None = None,
               date: str | None = None) -> dict:
    """結果を記録する。race_name(+date)でエントリを特定。"""
    entries = load_log(path)
    matches = [e for e in entries
               if e["race"]["name"] == race_name
               and (date is None or e["race"]["date"] == date)]
    if not matches:
        raise KeyError(f"ログに見つかりません: {race_name}")
    entry = matches[-1]
    names = {p["name"] for p in entry["picks"]}
    unknown = [n for n in order if n not in names]
    if unknown:
        raise ValueError(f"出走馬に無い馬名: {'、'.join(unknown)}")
    entry["result"] = {
        "order": order,
        "win_pay": win_pay,
        "place_pays": place_pays or {},
    }
    save_log(path, entries)
    return entry


def summarize(entries: list[dict]) -> dict:
    """ライブ成績の集計。回収率は払戻が入力済みのレースのみで計算する。"""
    finished = [e for e in entries if e.get("result")]
    rows = []
    n = wins = places = cover = box = 0
    tan_ret, tan_n = 0, 0
    fuku_ret, fuku_n = 0, 0
    for e in finished:
        res = e["result"]
        order = res["order"]
        top = e["picks"][0]
        top5 = {p["name"] for p in e["picks"][:5]}
        n += 1
        win = top["name"] == order[0]
        place = top["name"] in order[:3]
        wins += win
        places += place
        c = len(top5 & set(order[:3]))
        cover += c
        box += set(order[:3]) <= top5
        # 単勝回収: ◎的中時は払戻が必要。未入力なら回収計算から除外
        if not win:
            tan_n += 1
        elif res.get("win_pay"):
            tan_n += 1
            tan_ret += res["win_pay"]
        if not place:
            fuku_n += 1
        elif res.get("place_pays", {}).get(top["name"]):
            fuku_n += 1
            fuku_ret += res["place_pays"][top["name"]]
        finish_pos = order.index(top["name"]) + 1 if top["name"] in order else None
        rows.append({
            "date": e["race"]["date"], "name": e["race"]["name"],
            "top": top["name"], "top_finish": finish_pos,
            "cover3": c,
        })
    return {
        "n_logged": len(entries), "n_finished": n,
        "win_rate": wins / n if n else 0.0,
        "place_rate": places / n if n else 0.0,
        "cover3_avg": cover / n if n else 0.0,
        "box3_rate": box / n if n else 0.0,
        "tan_roi": tan_ret / (tan_n * 100) * 100 if tan_n else None,
        "tan_n": tan_n,
        "fuku_roi": fuku_ret / (fuku_n * 100) * 100 if fuku_n else None,
        "fuku_n": fuku_n,
        "rows": rows,
    }
