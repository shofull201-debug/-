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

import gzip
import json
import time
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
    checkpoint_path: str | Path | None = None,
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
    - checkpoint_path を指定すると開催日ごとに途中保存する
      （長時間の収集が中断しても、それまでの分が残る。
       取得済みHTMLはキャッシュされるため再実行も高速）。
    - 一時的な通信エラーはレース単位で3回までリトライし、
      それでも失敗したレースはスキップして続行する。
    """
    horse_cache: dict[str, ParsedHorse] = {}
    races_out: list[dict] = []
    failed: list[str] = []

    def fetch_with_retry(url: str) -> str | None:
        for attempt in range(3):
            try:
                return client.get(url)
            except Exception as e:  # 通信エラー等は指数バックオフでリトライ
                wait = 2 ** (attempt + 1)
                log(f"  取得失敗({e}) {wait}秒後にリトライ: {url}")
                time.sleep(wait)
        return None

    for yyyymmdd in _date_range(start, end):
        list_html = fetch_with_retry(race_list_url(yyyymmdd))
        if list_html is None:
            log(f"{yyyymmdd}: 開催日ページの取得に失敗、スキップ")
            continue
        race_ids = parse_race_ids(list_html)
        if not race_ids:
            continue
        log(f"{yyyymmdd}: {len(race_ids)} レース")

        for race_id in race_ids:
            if max_races is not None and len(races_out) >= max_races:
                log(f"上限 {max_races} レースに到達")
                return {"races": races_out}

            race_html = fetch_with_retry(race_url(race_id))
            if race_html is None:
                failed.append(race_id)
                continue
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

        # 開催日ごとに途中保存(中断対策)
        if checkpoint_path is not None:
            save_dataset({"races": races_out}, checkpoint_path)

    if failed:
        log(f"リトライしても取得できなかったレース: {len(failed)} 件 {failed[:5]}...")
    return {"races": races_out}


def save_dataset(dataset: dict, path: str | Path) -> None:
    """データセットを保存する。パスが .gz で終わる場合は gzip 圧縮する。"""
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=1)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=1)


def load_dataset(path: str | Path) -> dict:
    """データセットを読み込む。.gz 圧縮にも対応。"""
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)
