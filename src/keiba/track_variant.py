"""馬場指数の自動算出。

同日・同競馬場・同コース種別（芝/ダ）の全レースについて、
各出走馬の走破タイムと基準タイムの乖離を指数ポイントに換算し、

    レースごとの平均 → 日単位の中央値（外れ値に強い）

で「その日の馬場がどれだけ速い/遅いか」を推定する。

符号の規約はスピード指数の式に合わせる:
    指数 = (基準タイム − 走破タイム) × 距離指数 × 10 + 馬場指数 + …
- 時計のかかる馬場（タイムが基準より遅い）→ プラスの馬場指数で補正
- 高速馬場（タイムが基準より速い）→ マイナスの馬場指数で補正

算出した指数表は date|course|surface をキーに持ち、過去走の
track_variant が未設定（None）の走にだけ適用する。
これにより馬場状態（良/稍重…）からの概算より精密な補正になる。

注意: 基準タイムを実データで再構築した場合は、その基準タイムに対して
馬場指数を算出し直すこと（乖離の基準が変わるため）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Iterable

from .models import HorseEntry, PastRace, RaceCard
from .speed_index import base_time, distance_index

DEFAULT_MIN_RACES = 2   # 1日として採用する最小レース数
DEFAULT_CLAMP = 40.0    # 異常値対策の上下限（指数ポイント）


def day_key(date: str, course: str, surface: str) -> str:
    return f"{date}|{course}|{surface}"


def compute_variants(
    rows: Iterable[dict],
    min_races: int = DEFAULT_MIN_RACES,
    clamp: float = DEFAULT_CLAMP,
) -> dict[str, float]:
    """走破タイムの行データから馬場指数表を算出する。

    rows の各要素: {race_id, date, course, surface, distance, race_class, time_sec}
    戻り値: {"YYYY-MM-DD|競馬場|芝orダ": 馬場指数}
    """
    # レースごとに出走馬の乖離（指数ポイント）を集める
    per_race: dict[str, list[float]] = defaultdict(list)
    race_day: dict[str, str] = {}
    for row in rows:
        time_sec = row.get("time_sec")
        if not time_sec:
            continue
        base = base_time(row["course"], row["surface"], int(row["distance"]), row.get("race_class", "1勝"))
        di = distance_index(int(row["distance"]))
        deviation = (float(time_sec) - base) * 10 * di
        race_id = str(row.get("race_id") or f"{row['date']}|{row['course']}|{row['surface']}|{row['distance']}")
        per_race[race_id].append(deviation)
        race_day[race_id] = day_key(row["date"], row["course"], row["surface"])

    # 日単位に集約: レース平均の中央値
    per_day: dict[str, list[float]] = defaultdict(list)
    for race_id, deviations in per_race.items():
        per_day[race_day[race_id]].append(mean(deviations))

    variants = {}
    for key, race_means in per_day.items():
        if len(race_means) < min_races:
            continue
        value = max(-clamp, min(clamp, median(race_means)))
        variants[key] = round(value, 1)
    return variants


class VariantTable:
    """馬場指数表。date|course|surface → 指数ポイント。"""

    def __init__(self, table: dict[str, float] | None = None):
        self.table = dict(table or {})

    @classmethod
    def load(cls, path: str | Path) -> "VariantTable":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # {"variants": {...}} 形式と素の dict の両方を受ける
        return cls(data.get("variants", data) if isinstance(data, dict) else {})

    def save(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"variants": dict(sorted(self.table.items()))},
                f,
                ensure_ascii=False,
                indent=2,
            )

    def get(self, date: str, course: str, surface: str) -> float | None:
        return self.table.get(day_key(date, course, surface))

    # ---- 適用 ----------------------------------------------------------

    def apply_to_past_races(
        self, past_races: list[PastRace], overwrite: bool = False
    ) -> int:
        """過去走リストに馬場指数を適用する。適用した件数を返す。

        track_variant が明示されている走は overwrite=True でない限り触らない。
        """
        applied = 0
        for race in past_races:
            if race.track_variant is not None and not overwrite:
                continue
            variant = self.get(race.date, race.course, race.surface)
            if variant is not None:
                race.track_variant = variant
                applied += 1
        return applied

    def apply_to_horses(self, horses: list[HorseEntry], overwrite: bool = False) -> int:
        return sum(self.apply_to_past_races(h.past_races, overwrite) for h in horses)

    def apply_to_card(self, card: RaceCard, overwrite: bool = False) -> int:
        return self.apply_to_horses(card.horses, overwrite)
