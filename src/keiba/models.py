"""データモデル定義。

予想に必要な入力（出走馬・過去走・追切・レース条件）を dataclass で表現する。
JSON 入力からの構築は from_dict() を使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 馬場状態
GOINGS = ("良", "稍重", "重", "不良")

# クラス（下級 → 上級）
RACE_CLASSES = ("新馬", "未勝利", "1勝", "2勝", "3勝", "OP", "L", "G3", "G2", "G1")


@dataclass
class PastRace:
    """過去走 1 レース分の成績。"""

    date: str                 # "2026-05-10" など
    course: str               # 競馬場名（例: "東京"）
    surface: str              # "芝" or "ダ"
    distance: int             # メートル
    going: str                # 馬場状態（良/稍重/重/不良）
    time_sec: float           # 走破タイム（秒）例: 1:33.5 → 93.5
    weight_carried: float     # 斤量（kg）
    finish_position: int      # 着順
    field_size: int           # 出走頭数
    race_class: str           # クラス（RACE_CLASSES のいずれか）
    track_variant: float | None = None  # その日の馬場指数（指数ポイント）。不明なら None
    position_4c: int | None = None      # 4コーナー通過順位（脚質推定用）。不明なら None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PastRace":
        return cls(
            date=d["date"],
            course=d["course"],
            surface=d["surface"],
            distance=int(d["distance"]),
            going=d.get("going", "良"),
            time_sec=float(d["time_sec"]),
            weight_carried=float(d["weight_carried"]),
            finish_position=int(d.get("finish_position", 0)),
            field_size=int(d.get("field_size", 0)),
            race_class=d.get("race_class", "1勝"),
            track_variant=(
                float(d["track_variant"]) if d.get("track_variant") is not None else None
            ),
            position_4c=(
                int(d["position_4c"]) if d.get("position_4c") is not None else None
            ),
        )


@dataclass
class Workout:
    """追切 1 本分。

    course は "坂路" / "W"（ウッドチップ）/ "P"（ポリトラック）/ "芝" / "ダ" に正規化。
    total_time は 坂路なら 4F 通し、コース追いなら計測ハロン数（furlongs）の通しタイム。
    """

    date: str
    facility: str             # "美浦" or "栗東"（地方・外厩は "その他"）
    course: str               # "坂路" / "W" / "P" / "芝" / "ダ"
    furlongs: int             # 計測ハロン数（坂路=4, コース=5〜6 が一般的）
    total_time: float         # 通しタイム（秒）
    last_1f: float            # 終い 1F（秒）
    intensity: str = "馬なり"  # "一杯" / "強め" / "馬なり" / "G前仕掛け"
    partner_result: str | None = None  # 併せ馬の結果 "先着"/"同入"/"遅れ"、単走なら None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Workout":
        return cls(
            date=d["date"],
            facility=d.get("facility", "その他"),
            course=d["course"],
            furlongs=int(d.get("furlongs", 4)),
            total_time=float(d["total_time"]),
            last_1f=float(d["last_1f"]),
            intensity=d.get("intensity", "馬なり"),
            partner_result=d.get("partner_result"),
        )


@dataclass
class HorseEntry:
    """出走馬 1 頭分の情報。past_races は新しい順（直近が先頭）で持つ。"""

    name: str
    sire: str                          # 父
    dam_sire: str | None = None        # 母父
    weight_carried: float = 56.0       # 今回の斤量
    horse_number: int | None = None    # 馬番
    running_style: str | None = None   # 脚質（逃げ/先行/差し/追込）。None なら過去走から推定
    past_races: list[PastRace] = field(default_factory=list)
    workouts: list[Workout] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HorseEntry":
        past = [PastRace.from_dict(r) for r in d.get("past_races", [])]
        # 日付降順（直近が先頭）に揃える
        past.sort(key=lambda r: r.date, reverse=True)
        works = [Workout.from_dict(w) for w in d.get("workouts", [])]
        works.sort(key=lambda w: w.date, reverse=True)
        return cls(
            name=d["name"],
            sire=d.get("sire", ""),
            dam_sire=d.get("dam_sire"),
            weight_carried=float(d.get("weight_carried", 56.0)),
            horse_number=d.get("horse_number"),
            running_style=d.get("running_style"),
            past_races=past[:5],  # 過去 5 走まで
            workouts=works,
        )


@dataclass
class RaceInfo:
    """今回予想対象のレース条件。"""

    name: str
    date: str
    course: str
    surface: str
    distance: int
    going: str = "良"
    race_class: str = "OP"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RaceInfo":
        return cls(
            name=d.get("name", ""),
            date=d.get("date", ""),
            course=d["course"],
            surface=d["surface"],
            distance=int(d["distance"]),
            going=d.get("going", "良"),
            race_class=d.get("race_class", "OP"),
        )


@dataclass
class RaceCard:
    """レース条件 + 出走馬一覧（予想への入力単位）。"""

    race: RaceInfo
    horses: list[HorseEntry]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RaceCard":
        return cls(
            race=RaceInfo.from_dict(d["race"]),
            horses=[HorseEntry.from_dict(h) for h in d.get("horses", [])],
        )
