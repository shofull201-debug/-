"""データセット構築（scrape.dataset）のテスト。偽クライアントでネットワーク不要。"""

import pytest

pytest.importorskip("bs4")

from keiba.scrape.dataset import build_dataset  # noqa: E402
from keiba.scrape.netkeiba import horse_url, race_list_url, race_url  # noqa: E402
from test_netkeiba_parse import HORSE_HTML, LIST_HTML, RACE_HTML  # noqa: E402


class FakeClient:
    """URL→HTML の辞書を返す偽クライアント。取得履歴を記録する。"""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> str:
        self.requested.append(url)
        return self.pages.get(url, "<html><body></body></html>")


def make_pages() -> dict[str, str]:
    # 2026-05-24 のみ開催があり、レースは 202605021211 の1つ（フィクスチャと整合）
    pages = {race_list_url("20260524"): LIST_HTML.replace("202605021212", "202605021211").replace("202609021201", "202605021211")}
    pages[race_url("202605021211")] = RACE_HTML
    pages[horse_url("2021104567")] = HORSE_HTML
    pages[horse_url("2020105678")] = HORSE_HTML
    pages[horse_url("2021109999")] = HORSE_HTML
    return pages


class TestResultsOnlyMode:
    def test_no_horse_pages_fetched(self):
        client = FakeClient(make_pages())
        dataset = build_dataset(
            client, "2026-05-24", "2026-05-24", results_only=True, log=lambda m: None
        )
        assert len(dataset["races"]) == 1
        # 馬ページ(/horse/)へのリクエストが無いこと
        assert not any("/horse/" in u for u in client.requested)

    def test_all_horses_with_results(self):
        client = FakeClient(make_pages())
        dataset = build_dataset(
            client, "2026-05-24", "2026-05-24", results_only=True, log=lambda m: None
        )
        horses = dataset["races"][0]["horses"]
        assert len(horses) == 3  # 取消馬も含む(結果はNone)
        winner = horses[0]
        assert winner["name"] == "テストホース"
        assert winner["weight_carried"] == 57.0
        assert winner["result"]["time_sec"] == 92.5
        assert winner["result"]["finish_position"] == 1
        assert winner["result"]["odds"] == 4.5
        scratched = horses[2]
        assert scratched["result"]["finish_position"] is None

    def test_output_feeds_build_base_times(self, tmp_path):
        import json

        from keiba.cli import main

        client = FakeClient(make_pages())
        dataset = build_dataset(
            client, "2026-05-24", "2026-05-24", results_only=True, log=lambda m: None
        )
        path = tmp_path / "results.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False)
        out = tmp_path / "bt.json"
        assert main(["build-base-times", str(path), "-o", str(out), "--min-samples", "1"]) == 0
        with open(out, encoding="utf-8") as f:
            assert "東京|芝|1600" in json.load(f)["base_times"]


class TestFullMode:
    def test_horse_pages_fetched_and_past_races_attached(self):
        client = FakeClient(make_pages())
        dataset = build_dataset(
            client, "2026-05-24", "2026-05-24", min_past_races=1, log=lambda m: None
        )
        # 通常モードでは馬ページを取得する
        assert any("/horse/" in u for u in client.requested)
        # 出走頭数5未満のレースは除外されるので races は空
        assert dataset["races"] == []


class TestGzipRoundtrip:
    def test_save_and_load_gz(self, tmp_path):
        from keiba.scrape.dataset import load_dataset, save_dataset

        dataset = {"races": [{"race": {"course": "東京"}, "horses": []}]}
        path = tmp_path / "results.json.gz"
        save_dataset(dataset, path)
        assert load_dataset(path) == dataset
        # 素のJSONも従来どおり
        plain = tmp_path / "results.json"
        save_dataset(dataset, plain)
        assert load_dataset(plain) == dataset
