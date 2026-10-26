from datetime import datetime, timezone
import json

import httpx
import pytest

from shorts_factory.trend_research import (
    YouTubeClient, duration_seconds, fetch_google_trends_rss, import_trend_csv,
    research_trends, score_idea, SCORE_WEIGHTS,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def test_missing_key_never_contacts_network(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **k: pytest.fail("Unexpected network"))
    report = research_trends(["подводные кабели"], now=NOW)
    assert len(report["youtube"]) == 3
    assert {row["window"] for row in report["youtube"]} == {"72h", "7d", "30d"}
    assert all(row["status"] == "unavailable" and row["sample_size"] is None for row in report["youtube"])
    assert report["google_trends"]["status"] == "not_requested"
    assert report["youtube_requests"] == 0


def test_official_youtube_windows_metrics_and_short_caveat():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/search"):
            assert request.url.params["relevanceLanguage"] == "ru"
            assert request.url.params["videoDuration"] == "short"
            assert request.url.params["type"] == "video"
            return httpx.Response(200, json={"items": [{"id": {"videoId": "good"}}, {"id": {"channelId": "not-video"}},
                                                     {"id": {"videoId": "old"}}, {"id": {"videoId": "long"}}],
                                            "pageInfo": {"totalResults": 9999}, "nextPageToken": "sample"})
        return httpx.Response(200, json={"items": [
            {"id": "good", "snippet": {"title": "Кабель", "publishedAt": "2026-10-25T12:00:00Z", "channelId": "c"},
             "contentDetails": {"duration": "PT3M30S"}, "statistics": {"viewCount": "240", "likeCount": "10"}},
            {"id": "old", "snippet": {"publishedAt": "2020-01-01T00:00:00Z"}, "statistics": {"viewCount": "999999"}},
            {"id": "long", "snippet": {"publishedAt": "2026-10-25T12:00:00Z"}, "contentDetails": {"duration": "PT5M"}},
        ]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        api = YouTubeClient("do-not-save-me", client=session)
        report = research_trends(["кабель"], youtube_client=api, now=NOW)
    assert len(calls) == 6
    assert [call.url.params["publishedAfter"] for call in calls[::2]] == [
        "2026-10-23T12:00:00Z", "2026-10-19T12:00:00Z", "2026-09-26T12:00:00Z"]
    sample = report["youtube"][0]
    assert sample["sample_size"] == 1
    assert sample["approximate_search_results"] == 9999
    assert sample["truncated"] is True
    video = sample["videos"][0]
    assert video["views"] == 240 and video["engaged_views"] is None
    assert video["lifetime_views_per_hour_proxy"] == 10
    assert video["duration_candidate_under_3_minutes"] is False
    assert video["shorts_classification"] == "unverified"
    assert "do-not-save-me" not in json.dumps(report)


def test_youtube_failure_and_cap_are_honest_and_redacted():
    def handler(request):
        return httpx.Response(429, json={"error": "private-key-secret"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        api = YouTubeClient("private-key-secret", client=session, max_requests=1)
        report = research_trends(["кабель"], youtube_client=api, now=NOW)
    assert report["youtube_requests"] == 1
    assert all(row["status"] == "unavailable" for row in report["youtube"])
    assert "HTTP 429" in report["youtube"][0]["reason"]
    assert "limit reached" in report["youtube"][1]["reason"]
    assert "private-key-secret" not in json.dumps(report)


def test_youtube_timeout_and_bad_json_are_redacted():
    for failure in ("timeout", "bad-json"):
        def handler(request):
            if failure == "timeout":
                raise httpx.ReadTimeout("key=secret", request=request)
            return httpx.Response(200, text="not-json")
        with httpx.Client(transport=httpx.MockTransport(handler)) as session:
            api = YouTubeClient("secret", client=session)
            report = research_trends(["кабель"], windows=("72h",), youtube_client=api, now=NOW)
        assert report["youtube"][0]["status"] == "unavailable"
        assert "secret" not in json.dumps(report)


def test_google_rss_preserves_source_bucket_and_actual_date():
    rss = '''<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item>
    <title>Подводный кабель</title><link>https://trends.google.com/trends/explore?q=кабель</link>
    <pubDate>Mon, 26 Oct 2026 10:00:00 +0000</pubDate><ht:approx_traffic>20K+</ht:approx_traffic>
    <ht:news_item><ht:news_item_url>https://example.org/news</ht:news_item_url></ht:news_item>
    </item></channel></rss>'''
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=rss))) as session:
        report = fetch_google_trends_rss(geo="RU", now=NOW, client=session)
    row = report["records"][0]
    assert row["published_at"] == "2026-10-26T10:00:00Z"
    assert row["approximate_traffic_bucket"] == "20K+"
    assert row["language_audience_verified"] is False
    assert row["news_urls"] == ["https://example.org/news"]
    assert "geo=RU" in report["source_url"]


def test_google_rss_block_and_empty_region_do_not_substitute_other_country():
    for status, body, expected in [(403, "blocked", "unavailable"), (200, "<rss><channel/></rss>", "empty_or_region_unavailable"),
                                    (200, "<html>captcha</html>", "unavailable")]:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body))) as session:
            report = fetch_google_trends_rss(now=NOW, client=session)
        assert report["status"] == expected and report["records"] == []


def test_manual_trend_csv_preserves_dates_and_metrics_without_inventing(tmp_path):
    path = tmp_path / "google.csv"
    path.write_text('\ufeffTrends,Search volume,Started\nкабель,20K+,"October 26, 2026"\n', encoding="utf-8")
    report = import_trend_csv(path, source="google_trends", observed_at=NOW, geo="RU")
    assert report["records"][0]["topic"] == "кабель"
    assert report["records"][0]["metrics_as_exported"]["Search volume"] == "20K+"
    assert report["records"][0]["metrics_as_exported"]["Started"] == "October 26, 2026"
    path.write_text("hashtag;views;period;url\n#science;100000;7d;https://ads.tiktok.com/example\n", encoding="utf-8")
    tiktok = import_trend_csv(path, source="tiktok_creative_center", observed_at=NOW)
    assert tiktok["records"][0]["topic"] == "#science"
    assert tiktok["records"][0]["metrics_as_exported"]["period"] == "7d"


def test_google_explore_csv_is_relative_interest_not_views(tmp_path):
    path = tmp_path / "multiTimeline.csv"
    path.write_text('Category: All categories\n\nDay,кабель: (Россия)\n2026-10-25,100\n2026-10-26,<1\n', encoding="utf-8")
    report = import_trend_csv(path, source="google_trends", observed_at=NOW)
    assert report["status"] == "available"
    assert report["records"][1]["relative_interest_as_exported"] == "<1"
    assert report["records"][0]["period_as_exported"] == "2026-10-25"


def test_score_is_transparent_and_bounded():
    assert score_idea(SCORE_WEIGHTS)["score"] == 100
    assert "not predicted" in score_idea(SCORE_WEIGHTS)["meaning"]
    for bad in ({}, {**SCORE_WEIGHTS, "timeliness": 11}, {**SCORE_WEIGHTS, "timeliness": float("nan")},
                {**SCORE_WEIGHTS, "timeliness": True}):
        with pytest.raises(ValueError):
            score_idea(bad)
    assert duration_seconds("P1DT2H3M4.5S") == 93784.5
    assert duration_seconds("not-a-duration") is None


def test_two_snapshots_measure_actual_counter_change_and_keep_revisions():
    from shorts_factory.trend_research import compare_youtube_snapshots
    first = {"youtube": [{"videos": [
        {"video_id": "a", "views": 100, "observed_at": "2026-10-25T12:00:00Z"},
        {"video_id": "b", "views": 300, "observed_at": "2026-10-25T12:00:00Z"},
        {"video_id": "only-before", "views": 999, "observed_at": "2026-10-25T12:00:00Z"},
    ]}]}
    last = {"youtube": [{"videos": [
        {"video_id": "a", "views": 340, "observed_at": "2026-10-26T12:00:00Z"},
        {"video_id": "b", "views": 299, "observed_at": "2026-10-26T12:00:00Z"},
    ]}]}
    report = compare_youtube_snapshots(first, last)
    assert report["videos"][0]["views_counter_change"] == 240
    assert report["videos"][0]["observed_views_per_hour"] == 10
    assert report["videos"][1]["views_counter_change"] == -1
    assert report["videos"][1]["observed_views_per_hour"] is None
    assert report["videos"][1]["status"] == "counter_revision_not_growth"
    assert compare_youtube_snapshots(first, first)["status"] == "insufficient_overlapping_observations"


def test_malformed_optional_youtube_fields_do_not_fabricate_metrics():
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "bad"}}], "pageInfo": []})
        return httpx.Response(200, json={"items": [{"id": "bad", "snippet": {"publishedAt": []},
                                                   "contentDetails": {"duration": []}, "statistics": []}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        api = YouTubeClient("secret", client=session)
        report = api.search("кабель", "72h", now=NOW)
    assert report["sample_size"] == 0
    assert report["approximate_search_results"] is None
