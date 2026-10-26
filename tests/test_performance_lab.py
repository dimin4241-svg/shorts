from datetime import datetime, timezone
import csv
import json

import httpx
import pytest

from shorts_factory.performance_lab import import_studio_csv, import_analytics_api, analyze_performance

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def write_csv(path, headers, rows, delimiter=","):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(headers)
        writer.writerows(rows)


def test_english_csv_keeps_views_engaged_and_missing_values_separate(tmp_path):
    path = tmp_path / "Table data.csv"
    write_csv(path, ["Content", "Video title", "Video publish time", "Duration", "Views", "Engaged views",
                     "Impressions", "Shown in feed", "Average view duration", "Average percentage viewed (%)", "Subscribers"],
              [["Total", "", "", "", 999, 400, 30, 10, "0:25", 90, 9],
               ["v1", "Кабели", "Oct 25, 2026", 28, "1,200", 700, 100, "", "0:25", 89.3, -2],
               ["v2", "GPS", "2026-10-25T12:00:00+05:00", "0:40", 0, "—", 200, 400, "", "", ""]])
    rows = import_studio_csv(path, observed_at=NOW)
    assert len(rows) == 2
    first, second = rows
    assert first["metrics"]["views"] == 1200
    assert first["metrics"]["engaged_views"] == 700
    assert first["metrics"]["shorts_feed_impressions"] is None  # not thumbnail impressions
    assert first["metrics"]["rewatches"] is None
    assert first["metrics"]["average_view_duration_seconds"] == 25
    assert first["published_at"] == "2026-10-25"
    assert first["publication_precision"] == "date"
    assert first["metrics"]["subscribers_net"] == -2
    assert second["metrics"]["views"] == 0 and second["metrics"]["engaged_views"] is None
    assert second["published_at"] == "2026-10-25T07:00:00Z"
    assert second["duration_seconds"] == 40
    report = analyze_performance(rows, now=NOW)
    assert report["videos"][0]["sample_basis"] == "engaged_views"
    assert report["videos"][0]["uncertainty"] == "small_sample"
    assert report["videos"][0]["net_subscribers_per_1000_views"] == -1.667
    assert report["videos"][1]["net_subscribers_per_1000_views"] is None


def test_russian_semicolon_decimal_comma_and_no_fake_publish_time(tmp_path):
    path = tmp_path / "ru.csv"
    write_csv(path, ["Контент", "Название видео", "Дата публикации видео", "Продолжительность", "Просмотры",
                     "Заинтересованные просмотры", "Показано в ленте", "Продолжили смотреть (%)",
                     "Средняя продолжительность просмотра", "Средний процент просмотра (%)", "Новые подписчики"],
              [["v1", "Почему мороз", "25 окт. 2026 г.", "28", "1 234", "1\u202f000", "2 000", "62,5%", "0:27", "110,2", "4"]], ";")
    row = import_studio_csv(path, observed_at=NOW)[0]
    assert row["metrics"]["views"] == 1234
    assert row["metrics"]["engaged_views"] == 1000
    assert row["metrics"]["viewed_percent"] == 62.5
    assert row["metrics"]["average_percentage_viewed"] == 110.2
    assert row["metrics"]["rewatches"] is None
    assert row["published_at"] == "2026-10-25"
    assert row["source"]["row"] == 2
    assert "exact time" in row["warnings"][0]


def test_bad_values_are_null_and_report_exposes_unavailable(tmp_path):
    path = tmp_path / "bad.csv"
    write_csv(path, ["Video ID", "Views", "Engaged views", "Viewed (%)", "Duration", "Published at"],
              [["v", "NaN", "-1", "101", "garbage", "yesterday"]])
    row = import_studio_csv(path, observed_at=NOW)[0]
    assert all(row["metrics"][key] is None for key in ("views", "engaged_views", "viewed_percent"))
    assert row["duration_seconds"] is None and row["published_at"] is None
    assert "engaged_views" in row["unavailable_metrics"]
    assert row["warnings"]
    assert analyze_performance([row], now=NOW)["videos"][0]["uncertainty"] == "unknown_sample"


def test_comparison_requires_similar_duration_age_period_and_distinct_video(tmp_path):
    path = tmp_path / "cohorts.csv"
    write_csv(path, ["Video ID", "Duration", "Published at", "Views", "Engaged views", "Report start", "Report end", "Traffic source"],
              [["a", 28, "2026-10-24T12:00:00Z", 5000, 1000, "2026-10-24", "2026-10-26", "Shorts"],
               ["b", 30, "2026-10-24T12:00:00Z", 4000, 900, "2026-10-24", "2026-10-26", "Shorts"],
               ["too-long", 60, "2026-10-24T12:00:00Z", 5000, 3000, "2026-10-24", "2026-10-26", "Shorts"],
               ["too-old", 28, "2026-09-24T12:00:00Z", 5000, 3000, "2026-10-24", "2026-10-26", "Shorts"],
               ["other-period", 28, "2026-10-24T12:00:00Z", 5000, 3000, "2026-10-25", "2026-10-26", "Shorts"],
               ["search", 28, "2026-10-24T12:00:00Z", 5000, 3000, "2026-10-24", "2026-10-26", "Search"]])
    rows = import_studio_csv(path, observed_at=NOW)
    future_analysis = datetime(2027, 10, 26, tzinfo=timezone.utc)
    report = analyze_performance(rows, now=future_analysis)
    assert len(report["comparisons"]) == 1
    assert report["comparisons"][0]["left_row"] == 0 and report["comparisons"][0]["right_row"] == 1
    assert report["comparisons"][0]["small_sample"] is True
    assert report["videos"][0]["age_hours_at_observation"] == 48  # not age at analysis!
    assert report["suggestions"] == []


def test_counts_interval_uses_explicit_outcome_counts_not_views(tmp_path):
    path = tmp_path / "choices.csv"
    write_csv(path, ["Video ID", "Views", "Shown in feed", "Viewed count", "Swiped away count"],
              [["a", 99999, 9999, 60, 40], ["b", 99999, 9999, "", ""]])
    report = analyze_performance(import_studio_csv(path, observed_at=NOW), now=NOW)
    first, second = report["videos"]
    assert first["viewed_rate_percent_from_counts"] == 60
    assert first["viewed_rate_wilson_95_percent"] == [50.2, 69.06]
    assert second["viewed_rate_wilson_95_percent"] is None


def test_unknown_period_comparisons_do_not_create_recommendations(tmp_path):
    path = tmp_path / "unknown-period.csv"
    write_csv(path, ["Video ID", "Duration", "Published at", "Views", "Engaged views", "Viewed (%)"],
              [[str(index), 28, "2026-10-24T12:00:00Z", 10000, 5000, 20 + index * 10] for index in range(5)])
    report = analyze_performance(import_studio_csv(path, observed_at=NOW), now=NOW)
    assert len(report["comparisons"]) == 10
    assert all(pair["status"] == "preliminary_unknown_report_period" for pair in report["comparisons"])
    assert report["suggestions"] == []


def test_retention_is_user_supplied_not_invented(tmp_path):
    path = tmp_path / "retention.csv"
    points = [{"second": 2, "percentage": 81}, {"second": 1, "percentage": 90}]
    write_csv(path, ["Video ID", "retention"], [["a", json.dumps(points)]])
    row = import_studio_csv(path, observed_at=NOW)[0]
    assert row["retention"] == list(reversed(points))


def test_api_is_explicit_official_read_only_and_preserves_missing_metadata():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "youtubeanalytics.googleapis.com"
        assert request.headers["authorization"] == "Bearer secret-token"
        assert "engagedViews" in request.url.params["metrics"]
        return httpx.Response(200, json={"columnHeaders": [{"name": "video"}, {"name": "views"}, {"name": "engagedViews"}],
                                        "rows": [["id", 100, 40]]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        with pytest.raises(ValueError, match="explicit"):
            import_analytics_api(bearer_token="secret-token", start_date="2026-10-01", end_date="2026-10-26", client=session)
        rows = import_analytics_api(bearer_token="secret-token", start_date="2026-10-01", end_date="2026-10-26", client=session, opt_in=True, observed_at=NOW)
    assert len(requests) == 1
    assert rows[0]["metrics"]["views"] == 100 and rows[0]["metrics"]["engaged_views"] == 40
    assert rows[0]["published_at"] is None and rows[0]["duration_seconds"] is None
    assert rows[0]["metrics"]["shorts_feed_impressions"] is None
    assert rows[0]["report_start"] == "2026-10-01"
    assert rows[0]["observation_time_basis"] == "api_fetch_time"
    assert "secret-token" not in json.dumps(rows)


def test_api_errors_and_malformed_payloads_are_redacted():
    for status, payload in [(403, {"error": "secret-token"}), (200, {"columnHeaders": [], "rows": []}),
                             (200, {"columnHeaders": [{"name": "video"}], "rows": [["id", 99]]})]:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload))) as session:
            with pytest.raises(RuntimeError) as error:
                import_analytics_api(bearer_token="secret-token", start_date="2026-10-01", end_date="2026-10-26", client=session, opt_in=True)
        assert "secret-token" not in str(error.value)


def test_duration_and_visual_suggestions_require_four_sufficient_comparable_peers(tmp_path):
    path = tmp_path / "ready-cohort.csv"
    headers = ["Video ID", "Duration", "Published at", "Views", "Engaged views", "Average view duration",
               "Report start", "Report end"]
    data = [[str(index), 28, "2026-10-24T12:00:00Z", 10000, 5000, duration,
             "2026-10-24", "2026-10-26"] for index, duration in enumerate([12, 23, 24, 25, 26])]
    write_csv(path, headers, data)
    rows = import_studio_csv(path, observed_at=NOW)
    report = analyze_performance(rows, now=NOW)
    suggestions = [item for item in report["suggestions"] if item["video_id"] == "0"]
    assert {item["area"] for item in suggestions} == {"script_and_duration", "visual_story_and_pacing"}
    assert "average_duration_divided_by_length_proxy" in suggestions[0]["evidence"]
    assert "natural speech speed" in suggestions[0]["hypothesis"]
    assert "cannot locate" in suggestions[1]["caveat"]
    rows[-1]["metrics"]["engaged_views"] = 5
    assert analyze_performance(rows, now=NOW)["suggestions"] == []


def test_watch_percentage_suggestions_do_not_mix_different_measurement_bases(tmp_path):
    path = tmp_path / "percentage.csv"
    write_csv(path, ["Video ID", "Duration", "Published at", "Views", "Engaged views", "Average percentage viewed (%)",
                     "Report start", "Report end"],
              [[str(index), 28, "2026-10-24T12:00:00Z", 10000, 5000, pct, "2026-10-24", "2026-10-26"]
               for index, pct in enumerate([40, 90, 91, 92, 93])])
    rows = import_studio_csv(path, observed_at=NOW)
    report = analyze_performance(rows, now=NOW)
    assert any(item.get("area") == "script_and_duration" for item in report["suggestions"])
    rows[-1]["metrics"]["average_percentage_viewed"] = None
    rows[-1]["metrics"]["average_view_duration_seconds"] = 26
    assert analyze_performance(rows, now=NOW)["suggestions"] == []


def test_unknown_export_observation_time_disables_age_and_comparisons(tmp_path):
    path = tmp_path / "old-export.csv"
    write_csv(path, ["Video ID", "Duration", "Published at", "Views", "Engaged views", "Report start", "Report end"],
              [[identifier, 28, "2020-01-01T12:00:00Z", 5000, 3000, "2020-01-01", "2020-01-02"]
               for identifier in ("a", "b")])
    rows = import_studio_csv(path)
    assert all(row["observation_time_basis"] == "import_time_export_date_unknown" for row in rows)
    assert all(any("Export observation time unknown" in warning for warning in row["warnings"]) for row in rows)
    report = analyze_performance(rows, now=NOW)
    assert report["comparisons"] == []
    assert all(row["age_hours_at_observation"] is None for row in report["videos"])
    assert all(row["age_precision"] == "export_observation_time_unknown" for row in report["videos"])
    explicit = import_studio_csv(path, observed_at=datetime(2020, 1, 2, 12, tzinfo=timezone.utc))
    assert explicit[0]["observation_time_basis"] == "explicit_export_observation"
    assert len(analyze_performance(explicit, now=NOW)["comparisons"]) == 1
