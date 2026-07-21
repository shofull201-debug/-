"""調教索引(TARGET出力)からのレースカード/データセットへの適用。

索引は {馬名: [[日付, 施設, 通し, 終い1F, コース, ハロン数], ...]}(日付昇順)。
旧形式の4要素([日付, 施設, 4F通し, 終い1F])は坂路4Fとして解釈する。
レース日の直前 days 日以内から、直近1本+それ以外のベスト1本
(1Fあたりペースが最速)の最大2本を Workout 形式にする。

坂路は「好タイムのみ」の抽出データなので、載っていない馬は
「速い時計を出していない」か「コース追い中心」のどちらか。
予想側では欠損(追切なし)として扱われ、重みは他要素へ再配分される。
"""

from __future__ import annotations

from datetime import date as _date

DEFAULT_DAYS = 21   # レース前何日までの調教を使うか
MAX_WORKS = 2


def _to_date(s: str) -> _date:
    return _date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def workouts_for(
    index: dict, name: str, race_date: str, days: int = DEFAULT_DAYS
) -> list[dict]:
    """馬名とレース日から Workout dict のリスト(新しい順)を返す。"""
    entries = index.get(name)
    if not entries:
        return []
    rd = _to_date(race_date)
    window = [
        e for e in entries
        if 0 < (rd - _to_date(e[0])).days <= days
    ]
    if not window:
        return []
    def pace(e) -> float:  # 1Fあたりの通しペース(坂路/コース混在の比較用)
        furlongs = e[5] if len(e) > 5 else 4
        return e[2] / furlongs

    window.sort(key=lambda e: e[0])
    latest = window[-1]
    picks = [latest]
    others = window[:-1]
    if others:
        picks.append(min(others, key=pace))

    return [
        {
            "date": e[0],
            "facility": e[1],
            "course": e[4] if len(e) > 4 else "坂路",
            "furlongs": e[5] if len(e) > 5 else 4,
            "total_time": e[2],
            "last_1f": e[3],
            "intensity": "馬なり",
        }
        for e in picks
    ]


def attach_to_card(card_data: dict, index: dict, days: int = DEFAULT_DAYS,
                   replace: bool = False) -> int:
    """カードの各馬に調教索引から追切を設定する。"""
    race_date = card_data["race"]["date"]
    applied = 0
    for horse in card_data.get("horses", []):
        if horse.get("workouts") and not replace:
            continue
        works = workouts_for(index, horse["name"], race_date, days)
        if works:
            horse["workouts"] = works
            applied += 1
    return applied
