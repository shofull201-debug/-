"""netkeiba（db.netkeiba.com）のページ取得とパース。

注意:
- 個人の研究用途を想定。netkeiba の利用規約を確認し、自己責任で利用すること。
- デフォルトで 1.5 秒/リクエストのウェイトを入れ、取得済みページは
  ディスクにキャッシュして再取得しない（節度あるアクセスのため）。
- db.netkeiba.com は EUC-JP エンコーディング。

レース ID の構造: YYYY + 場コード(2) + 開催回(2) + 日目(2) + レース番号(2)
例: 202505021211 = 2025年 東京(05) 2回 12日目 11R
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

BASE_URL = "https://db.netkeiba.com"

# 場コード → 競馬場名
PLACE_CODES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}


def _require_bs4():
    try:
        from bs4 import BeautifulSoup  # noqa: F401
        return BeautifulSoup
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "スクレイピングには beautifulsoup4 が必要です: pip install beautifulsoup4"
        ) from e


class NetkeibaClient:
    """キャッシュとレート制限つきの HTTP クライアント。"""

    def __init__(self, cache_dir: str | Path = "data/cache", wait_sec: float = 1.5):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.wait_sec = wait_sec
        self._last_fetch = 0.0

    def _cache_path(self, url: str) -> Path:
        key = re.sub(r"[^0-9A-Za-z]+", "_", url.removeprefix("https://")).strip("_")
        return self.cache_dir / f"{key}.html"

    def get(self, url: str) -> str:
        """URL の HTML を返す。キャッシュがあればそれを使う。"""
        cache = self._cache_path(url)
        if cache.exists():
            return cache.read_text(encoding="utf-8")

        elapsed = time.time() - self._last_fetch
        if elapsed < self.wait_sec:
            time.sleep(self.wait_sec - elapsed)
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
        self._last_fetch = time.time()

        html = raw.decode("euc-jp", errors="replace")
        cache.write_text(html, encoding="utf-8")
        return html


# ---- パース結果のコンテナ ---------------------------------------------------

@dataclass
class RaceResultRow:
    """レース結果 1 頭分。"""

    finish_position: int | None       # 取消・中止などは None
    horse_number: int
    name: str
    horse_id: str
    weight_carried: float
    time_sec: float | None
    odds: float | None                # 単勝オッズ
    popularity: int | None            # 人気


@dataclass
class ParsedRace:
    """レース結果ページのパース結果。"""

    race_id: str
    name: str
    date: str                         # "2026-05-24"
    course: str
    surface: str                      # "芝" / "ダ" / "障"
    distance: int
    going: str
    race_class: str
    rows: list[RaceResultRow] = field(default_factory=list)


@dataclass
class ParsedHorse:
    """競走馬ページのパース結果。"""

    horse_id: str
    sire: str
    dam_sire: str | None
    past_races: list[dict] = field(default_factory=list)  # PastRace 互換 dict（新しい順）


# ---- 共通ヘルパー -----------------------------------------------------------

# 先頭から順にマッチさせるため、GIII → GII → GI の順（GI の誤爆防止）
CLASS_PATTERNS: list[tuple[str, str]] = [
    ("GIII", "G3"), ("G3", "G3"), ("Jpn3", "G3"),
    ("GII", "G2"), ("G2", "G2"), ("Jpn2", "G2"),
    ("GI", "G1"), ("G1", "G1"), ("Jpn1", "G1"),
    ("(L)", "L"), ("リステッド", "L"),
    ("オープン", "OP"), ("OP", "OP"),
    ("3勝", "3勝"), ("1600万", "3勝"),
    ("2勝", "2勝"), ("1000万", "2勝"),
    ("1勝", "1勝"), ("500万", "1勝"),
    ("未勝利", "未勝利"),
    ("新馬", "新馬"),
]


def detect_class(text: str) -> str:
    """レース名や条件文からクラスを推定する。判定不能なら '1勝'。"""
    for pat, cls in CLASS_PATTERNS:
        if pat in text:
            return cls
    return "1勝"


def parse_time(text: str) -> float | None:
    """'1:32.5' や '58.9' を秒に変換する。"""
    text = text.strip()
    m = re.match(r"^(?:(\d+):)?(\d+\.\d+)$", text)
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    return minutes * 60 + float(m.group(2))


def _to_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() else None


def _to_float(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


# ---- レース結果ページ --------------------------------------------------------

def race_url(race_id: str) -> str:
    return f"{BASE_URL}/race/{race_id}/"


def parse_race_page(html: str, race_id: str = "") -> ParsedRace | None:
    """db.netkeiba.com のレース結果ページをパースする。障害戦などは None。"""
    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")

    intro = soup.select_one("div.data_intro")
    if intro is None:
        return None
    h1 = intro.find("h1")
    name = h1.get_text(strip=True) if h1 else ""

    # 例: "芝右1600m / 天候 : 晴 / 芝 : 良 / 発走 : 15:35"
    cond_text = " ".join(s.get_text(" ", strip=True) for s in intro.find_all("span"))
    m = re.search(r"(芝|ダ|障)\D*?(\d{3,4})m", cond_text)
    if not m:
        return None
    surface = m.group(1)
    if surface == "障":
        return None  # 障害戦は対象外
    distance = int(m.group(2))
    mg = re.search(r"(?:芝|ダート)\s*:\s*(良|稍重|重|不良)", cond_text)
    going = mg.group(1) if mg else "良"

    # 例: "2026年5月24日 2回東京12日目 3歳以上オープン"
    small = soup.select_one("p.smalltxt")
    small_text = small.get_text(" ", strip=True) if small else ""
    md = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", small_text)
    date = f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}" if md else ""
    mc = re.search(r"\d+回(\S{2})\d+日", small_text)
    course = mc.group(1) if mc else PLACE_CODES.get(race_id[4:6], "")

    race_class = detect_class(name + " " + small_text)

    table = soup.select_one("table.race_table_01")
    if table is None:
        return None
    rows_tr = table.find_all("tr")
    if not rows_tr:
        return None

    headers = [th.get_text(strip=True) for th in rows_tr[0].find_all("th")]

    def col(label: str) -> int | None:
        for i, h in enumerate(headers):
            if label in h:
                return i
        return None

    idx = {
        "finish": col("着順"), "num": col("馬番"), "name": col("馬名"),
        "weight": col("斤量"), "time": col("タイム"), "odds": col("単勝"),
        "pop": col("人気"),
    }
    if idx["finish"] is None or idx["name"] is None:
        return None

    result_rows: list[RaceResultRow] = []
    for tr in rows_tr[1:]:
        tds = tr.find_all("td")
        if len(tds) <= max(i for i in idx.values() if i is not None):
            continue

        def cell(key: str) -> str:
            i = idx[key]
            return tds[i].get_text(strip=True) if i is not None else ""

        link = tds[idx["name"]].find("a", href=re.compile(r"/horse/(\w+)"))
        horse_id = ""
        if link:
            mh = re.search(r"/horse/(\w+)", link["href"])
            horse_id = mh.group(1) if mh else ""

        result_rows.append(
            RaceResultRow(
                finish_position=_to_int(cell("finish")),
                horse_number=_to_int(cell("num")) or 0,
                name=cell("name"),
                horse_id=horse_id,
                weight_carried=_to_float(cell("weight")) or 56.0,
                time_sec=parse_time(cell("time")),
                odds=_to_float(cell("odds")),
                popularity=_to_int(cell("pop")),
            )
        )

    return ParsedRace(
        race_id=race_id,
        name=name,
        date=date,
        course=course,
        surface=surface,
        distance=distance,
        going=going,
        race_class=race_class,
        rows=result_rows,
    )


# ---- 開催日のレース ID 一覧 ---------------------------------------------------

def race_list_url(yyyymmdd: str) -> str:
    return f"{BASE_URL}/race/list/{yyyymmdd}/"


def parse_race_ids(html: str) -> list[str]:
    """開催日ページから 12 桁のレース ID を抽出する。"""
    return sorted(set(re.findall(r"/race/(\d{12})/", html)))


# ---- 競走馬ページ -------------------------------------------------------------

def horse_url(horse_id: str) -> str:
    return f"{BASE_URL}/horse/{horse_id}/"


def parse_horse_page(html: str, horse_id: str = "") -> ParsedHorse | None:
    """競走馬ページから血統（父・母父）と過去成績をパースする。"""
    BeautifulSoup = _require_bs4()
    soup = BeautifulSoup(html, "html.parser")

    # 血統表: 1行目先頭セル=父、3行目末尾セル=母父
    blood = soup.select_one("table.blood_table")
    sire, dam_sire = "", None
    if blood:
        trs = blood.find_all("tr")
        if len(trs) >= 3:
            tds0 = trs[0].find_all("td")
            if tds0:
                sire = tds0[0].get_text(" ", strip=True).split()[0] if tds0[0].get_text(strip=True) else ""
            tds2 = trs[2].find_all("td")
            if tds2:
                text = tds2[-1].get_text(" ", strip=True)
                dam_sire = text.split()[0] if text else None

    table = soup.select_one("table.db_h_race_results")
    past: list[dict] = []
    if table:
        trs = table.find_all("tr")
        headers = [th.get_text(strip=True) for th in trs[0].find_all("th")] if trs else []

        def col(label: str) -> int | None:
            for i, h in enumerate(headers):
                if label in h:
                    return i
            return None

        idx = {
            "date": col("日付"), "place": col("開催"), "race_name": col("レース名"),
            "field": col("頭数"), "finish": col("着順"), "weight": col("斤量"),
            "dist": col("距離"), "going": col("馬場"), "time": col("タイム"),
        }
        for tr in trs[1:]:
            tds = tr.find_all("td")
            needed = [i for i in idx.values() if i is not None]
            if not needed or len(tds) <= max(needed):
                continue

            def cell(key: str) -> str:
                i = idx[key]
                return tds[i].get_text(strip=True) if i is not None else ""

            md = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", cell("date"))
            dm = re.match(r"(芝|ダ|障)(\d{3,4})", cell("dist"))
            time_sec = parse_time(cell("time"))
            finish = _to_int(cell("finish"))
            if not (md and dm and time_sec and finish):
                continue  # 取消・障害・海外などはスキップ
            if dm.group(1) == "障":
                continue
            course = re.sub(r"[0-9]", "", cell("place"))
            past.append(
                {
                    "date": f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}",
                    "course": course,
                    "surface": dm.group(1),
                    "distance": int(dm.group(2)),
                    "going": cell("going") or "良",
                    "time_sec": time_sec,
                    "weight_carried": _to_float(cell("weight")) or 56.0,
                    "finish_position": finish,
                    "field_size": _to_int(cell("field")) or 0,
                    "race_class": detect_class(cell("race_name")),
                }
            )

    past.sort(key=lambda r: r["date"], reverse=True)
    return ParsedHorse(horse_id=horse_id, sire=sire, dam_sire=dam_sire, past_races=past)
