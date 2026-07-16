"""netkeiba からバックテスト用データセットを構築する。

データセット JSON の形式:
{
  "races": [
    {
      "race": { RaceInfo 互換 + "race_id" },
      "horses": [
        { HorseEntry 互換（past_races は当該レースより前の 5 走）
          + "result": {"finish_position", "time_sec", "odds", "popularity"} }
      ]
    }
  ]
}

race カード部分は keiba.models.RaceCard.from_dict でそのまま読める。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .netkeiba import (
    NetkeibaClient,
    ParsedHorse,
    horse_url,
    parse_horse_page,
    parse_payouts,
    parse_race_ids,
    parse_race_page,
    race_list_url,
    race_url,
)


def _date_range(start: str, end: str):
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while d <= stop:
        yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def build_dataset(
    client: NetkeibaClient,
    start: str,
    end: str,
    surface: str | None = None,
    max_races: int | None = None,
    min_past_races: int = 2,
    results_only: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """期間内の JRA レースを取得し、各出走馬の過去 5 走・血統つきデータセットを作る。

    - surface: "芝" / "ダ" で絞り込み（None なら両方）
    - min_past_races: 過去走がこの数未満の馬はスキップ（新馬戦対策）
    - 馬ページはキャッシュされるため、同じ馬が何度出走していても取得は 1 回
    - results_only=True の場合は馬ページを取得せず、レース結果
      （全馬の走破タイム・斤量・着順・オッズ）だけを収集する。
      リクエスト数が「開催日数 + レース数」だけで済むため大量収集向き。
      出力は build-base-times / build-variants にそのまま使える。
    """
    horse_cache: dict[str, ParsedHorse] = {}
    races_out: list[dict] = []

    for yyyymmdd in _date_range(start, end):
        race_ids = parse_race_ids(client.get(race_list_url(yyyymmdd)))
        if not race_ids:
            continue
        log(f"{yyyymmdd}: {len(race_ids)} レース")

        for race_id in race_ids:
            if max_races is not None and len(races_out) >= max_races:
                log(f"上限 {max_races} レースに到達")
                return {"races": races_out}

            race_html = client.get(race_url(race_id))
            parsed = parse_race_page(race_html, race_id)
            if parsed is None or not parsed.date:
                continue
            if surface and parsed.surface != surface:
                continue

            if results_only:
                races_out.append(
                    {
                        "payouts": parse_payouts(race_html),
                        "race": {
                            "race_id": parsed.race_id,
                            "name": parsed.name,
                            "date": parsed.date,
                            "course": parsed.course,
                            "surface": parsed.surface,
                            "distance": parsed.distance,
                            "going": parsed.going,
                            "race_class": parsed.race_class,
                        },
                        "horses": [
                            {
                                "name": row.name,
                                "horse_number": row.horse_number,
                                "horse_id": row.horse_id,
                                "sire": "",
                                "weight_carried": row.weight_carried,
                                "past_races": [],
                                "workouts": [],
                                "result": {
                                    "finish_position": row.finish_position,
                                    "time_sec": row.time_sec,
                                    "odds": row.odds,
                                    "popularity": row.popularity,
                                },
                            }
                            for row in parsed.rows
                        ],
                    }
                )
                continue

            horses = []
            for row in parsed.rows:
                if not row.horse_id:
                    continue
                if row.horse_id not in horse_cache:
                    ph = parse_horse_page(client.get(horse_url(row.horse_id)), row.horse_id)
                    horse_cache[row.horse_id] = ph
                ph = horse_cache[row.horse_id]
                if ph is None:
                    continue
                # 当該レースより前の走歴のみを使う（リーク防止）
                past = [r for r in ph.past_races if r["date"] < parsed.date][:5]
                if len(past) < min_past_races:
                    continue
                horses.append(
                    {
                        "name": row.name,
                        "horse_number": row.horse_number,
                        "sire": ph.sire,
                        "dam_sire": ph.dam_sire,
                        "weight_carried": row.weight_carried,
                        "past_races": past,
                        "workouts": [],
                        "result": {
                            "finish_position": row.finish_position,
                            "time_sec": row.time_sec,
                            "odds": row.odds,
                            "popularity": row.popularity,
                        },
                    }
                )

            if len(horses) < 5:
                continue  # 評価対象馬が少なすぎるレースは除外
            races_out.append(
                {
                    "race": {
                        "race_id": parsed.race_id,
                        "name": parsed.name,
                        "date": parsed.date,
                        "course": parsed.course,
                        "surface": parsed.surface,
                        "distance": parsed.distance,
                        "going": parsed.going,
                        "race_class": parsed.race_class,
                    },
                    "horses": horses,
                }
            )

    return {"races": races_out}


def save_dataset(dataset: dict, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=1)
