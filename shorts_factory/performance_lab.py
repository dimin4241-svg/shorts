"""Import creator-owned analytics without equating views with engaged views.

CSV is the default. API reads require an explicit opt-in and an already issued
OAuth bearer token; no credentials, login flows or artificial engagement here.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from statistics import median

import httpx

METRIC_ALIASES = {
    "views": ("views", "просмотры"),
    "engaged_views": ("engaged views", "engagedViews", "заинтересованные просмотры", "вовлеченные просмотры"),
    "shorts_feed_impressions": ("shown in feed", "shorts feed impressions", "показано в ленте", "показы в ленте shorts", "показы в ленте"),
    "viewed_percent": ("viewed (%)", "viewed vs swiped away (%)", "viewed (vs swiped away)", "stayed to watch (%)", "stayed to watch", "продолжили смотреть (%)", "продолжили смотреть", "выбрали просмотр (%)", "просмотрено (%)"),
    "swiped_away_percent": ("swiped away (%)", "swiped away", "пролистали (%)", "пролистали"),
    "viewed_count": ("viewed count", "число выбравших просмотр"),
    "swiped_away_count": ("swiped away count", "число пролистываний"),
    "average_view_duration_seconds": ("average view duration", "averageViewDuration", "average view duration (seconds)", "средняя продолжительность просмотра", "средняя длительность просмотра"),
    "average_percentage_viewed": ("average percentage viewed (%)", "average percentage viewed", "averageViewPercentage", "средний процент просмотра (%)", "средний процент просмотра"),
    "rewatches": ("rewatches", "repeat views", "повторные просмотры"),
    "subscribers_gained": ("subscribers gained", "subscribersGained", "новые подписчики", "полученные подписчики"),
    "subscribers_lost": ("subscribers lost", "subscribersLost", "потерянные подписчики"),
    "subscribers_net": ("subscribers", "net subscribers", "подписчики", "изменение числа подписчиков"),
}
FIELD_ALIASES = {
    "video_id": ("video", "video id", "video_id", "content", "контент", "идентификатор видео", "идентификатор ролика"),
    "title": ("video title", "title", "название видео", "название"),
    "duration_seconds": ("duration", "duration (seconds)", "duration_seconds", "длительность", "продолжительность", "продолжительность (сек.)"),
    "published_at": ("video publish time", "publish time", "published at", "published_at", "publication date", "дата публикации видео", "время публикации видео", "дата публикации", "дата и время публикации"),
    "traffic_source": ("traffic source", "traffic source type", "insightTrafficSourceType", "источник трафика", "тип источника трафика"),
    "report_start": ("report start", "start date", "report_start", "начало периода"),
    "report_end": ("report end", "end date", "report_end", "конец периода"),
    "retention": ("retention", "retention_seconds", "удержание по секундам"),
}
COUNT_METRICS = {"views", "engaged_views", "shorts_feed_impressions", "viewed_count", "swiped_away_count",
                 "rewatches", "subscribers_gained", "subscribers_lost", "subscribers_net"}
BOUNDED_PERCENT_METRICS = {"viewed_percent", "swiped_away_percent"}


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lstrip("\ufeff").casefold().replace("ё", "е"))


def _current(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _number(value: object, *, integral: bool = False) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "").removesuffix("%")
    if raw.lower() in {"", "-", "—", "--", "n/a", "na", "null", "nan", "н/д"}:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        # Integral export columns can contain English thousands groups.
        raw = raw.replace(",", "") if integral and re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", raw) else raw.replace(",", ".")
    try:
        parsed = float(raw)
    except ValueError:
        return None
    if not math.isfinite(parsed) or (integral and not parsed.is_integer()):
        return None
    return int(parsed) if integral else parsed


def _duration(value: object) -> float | None:
    raw = str(value or "").strip()
    if ":" not in raw:
        result = _number(value)
        return float(result) if result is not None and result >= 0 else None
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        return None
    values = [_number(part) for part in parts]
    if any(part is None or part < 0 for part in values) or any(part >= 60 for part in values[1:]):
        return None
    return float(sum(part * 60 ** i for i, part in enumerate(reversed(values))))


_MONTHS_RU = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
              "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
_MONTHS_EN = {name: i for i, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _publish(value: object) -> tuple[str | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if "T" not in raw and " " not in raw:
            return parsed.date().isoformat(), "date"
        if parsed.tzinfo:
            return _iso(parsed.astimezone(timezone.utc)), "timestamp"
        return parsed.isoformat(), "local_timestamp_timezone_unknown"
    except ValueError:
        pass
    for pattern in ("%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat(), "date"
        except ValueError:
            pass
    # Localized Studio exports: "Oct 26, 2026", "26 окт. 2026 г.".
    cleaned = raw.lower().replace(",", "").replace(".", "")
    ru = re.fullmatch(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})(?:\s*г)?", cleaned)
    en = re.fullmatch(r"([a-z]+)\s+(\d{1,2})\s+(\d{4})", cleaned)
    try:
        if ru and ru[2][:3] in _MONTHS_RU:
            return date(int(ru[3]), _MONTHS_RU[ru[2][:3]], int(ru[1])).isoformat(), "date"
        if en and en[1][:3] in _MONTHS_EN:
            return date(int(en[3]), _MONTHS_EN[en[1][:3]], int(en[2])).isoformat(), "date"
    except ValueError:
        pass
    return None, None


def _lookup(raw: dict, aliases: tuple[str, ...]) -> object:
    normalized = {_normal(key): value for key, value in raw.items() if key is not None}
    return next((normalized[_normal(alias)] for alias in aliases if _normal(alias) in normalized), None)


def _retention(raw: object) -> list[dict] | None:
    if raw in (None, ""):
        return None
    try:
        points = json.loads(str(raw)) if isinstance(raw, str) else raw
        if not isinstance(points, list):
            return None
        result = []
        for point in points:
            second, percentage = _number(point.get("second")), _number(point.get("percentage"))
            if second is None or percentage is None or min(second, percentage) < 0:
                return None
            result.append({"second": second, "percentage": percentage})
        return sorted(result, key=lambda item: item["second"]) or None
    except (ValueError, TypeError, AttributeError):
        return None


def _record(raw: dict, *, source: dict, observed_at: datetime, observation_time_basis: str) -> dict:
    warnings = []
    metrics = {}
    for metric, aliases in METRIC_ALIASES.items():
        cell = _lookup(raw, aliases)
        parsed = _duration(cell) if metric == "average_view_duration_seconds" else _number(cell, integral=metric in COUNT_METRICS)
        if parsed is not None and (parsed < 0 and metric != "subscribers_net" or metric in BOUNDED_PERCENT_METRICS and parsed > 100):
            parsed = None
        metrics[metric] = parsed
        if cell not in (None, "", "—", "-", "--", "N/A") and parsed is None:
            warnings.append(f"Unusable value for {metric}; retained as unavailable")
    fields = {field: _lookup(raw, aliases) for field, aliases in FIELD_ALIASES.items()}
    published, precision = _publish(fields["published_at"])
    if fields["published_at"] and published is None:
        warnings.append("Unrecognized publication date; age comparison unavailable")
    if precision == "date":
        warnings.append("Publication day is known; exact time and timezone were not exported")
    if precision == "local_timestamp_timezone_unknown":
        warnings.append("Publication timezone unknown; age comparison unavailable")
    duration = _duration(fields["duration_seconds"])
    if duration == 0:
        duration = None
    retention = _retention(fields["retention"])
    if fields["retention"] and retention is None:
        warnings.append("Unrecognized retention points; expected JSON second/percentage objects")
    return {"video_id": str(fields["video_id"]).strip() if fields["video_id"] else None,
            "title": str(fields["title"]).strip() if fields["title"] else None,
            "duration_seconds": duration, "published_at": published, "publication_precision": precision,
            "observed_at": _iso(observed_at), "observation_time_basis": observation_time_basis,
            "report_start": _publish(fields["report_start"])[0],
            "report_end": _publish(fields["report_end"])[0], "traffic_source": fields["traffic_source"] or None,
            "metrics": metrics, "retention": retention, "source": source,
            "unavailable_metrics": [key for key, value in metrics.items() if value is None], "warnings": warnings}


def import_studio_csv(path: str | Path, *, observed_at: datetime | None = None) -> list[dict]:
    """Read English/Russian UTF-8 Studio table exports, including BOM and ';'.

    Aggregate totals are skipped. No missing metric defaults to zero. Thumbnail
    'Impressions' deliberately does not map to 'Shown in feed'. No locale guesses
    invent a publication time or an API-only metric. Without explicit observed_at,
    the timestamp records import time only and cannot support age comparisons.
    """
    current = _current(observed_at)
    content = Path(path).read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    headers = {_normal(key) for key in reader.fieldnames or []}
    identifiers = {_normal(alias) for alias in FIELD_ALIASES["video_id"] + FIELD_ALIASES["title"]}
    if not headers.intersection(identifiers):
        raise ValueError("Studio CSV needs a Content/Video ID or Video title column; export the per-video table")
    records = []
    for index, raw in enumerate(reader, 2):
        if None in raw:
            raise ValueError(f"Malformed Studio CSV row {index}: unexpected columns")
        identity = _lookup(raw, FIELD_ALIASES["video_id"])
        title = _lookup(raw, FIELD_ALIASES["title"])
        if not identity and not title:
            continue
        if _normal(identity or "") in {"total", "totals", "итого", "всего"} or _normal(title or "") in {"total", "totals", "итого", "всего"}:
            continue
        row = _record(raw, source={"type": "youtube_studio_csv", "file": Path(path).name, "row": index},
                      observed_at=current,
                      observation_time_basis="explicit_export_observation" if observed_at is not None
                      else "import_time_export_date_unknown")
        if observed_at is None:
            row["warnings"].append("Export observation time unknown; timestamp is import time only. Age comparisons require explicit observed_at from the original export")
        records.append(row)
    return records


def import_analytics_api(*, bearer_token: str, start_date: str, end_date: str,
                         opt_in: bool = False, client: httpx.Client | None = None,
                         observed_at: datetime | None = None) -> list[dict]:
    """One bounded official channel report (up to 200 videos), OAuth read only.

    Token requires yt-analytics.readonly. Publication times/durations and feed
    choice metrics are absent in this report and remain null; merge manually
    with a Studio per-video export before age/duration comparisons.
    """
    if not opt_in:
        raise ValueError("Official Analytics API requires explicit opt_in=True")
    if not bearer_token.strip() or "\n" in bearer_token or "\r" in bearer_token:
        raise ValueError("An existing OAuth bearer token is required")
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must not follow end_date")
    current = _current(observed_at)
    params = {"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
              "dimensions": "video", "metrics": "views,engagedViews,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost",
              "sort": "-views", "maxResults": 200}
    headers = {"Authorization": f"Bearer {bearer_token.strip()}"}
    try:
        if client is None:
            with httpx.Client(timeout=30, follow_redirects=False) as session:
                response = session.get("https://youtubeanalytics.googleapis.com/v2/reports", params=params, headers=headers)
        else:
            response = client.get("https://youtubeanalytics.googleapis.com/v2/reports", params=params, headers=headers,
                                  timeout=30, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        columns, rows = payload.get("columnHeaders"), payload.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ValueError("Missing report columns")
        names = [column["name"] for column in columns]
        if "video" not in names:
            raise ValueError("Missing video dimension")
        records = []
        for values in rows:
            if not isinstance(values, list) or len(values) != len(names):
                raise ValueError("Invalid report row")
            raw = dict(zip(names, values))
            raw.update({"report start": start.isoformat(), "report end": end.isoformat()})
            row = _record(raw, source={"type": "youtube_analytics_api", "report_url": "https://youtubeanalytics.googleapis.com/v2/reports"},
                          observed_at=current, observation_time_basis="api_fetch_time")
            row["warnings"].append("API sample limited to 200 highest-view videos; duration and publication metadata unavailable")
            records.append(row)
        return records
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"YouTube Analytics API HTTP {exc.response.status_code}; check OAuth permission/report compatibility") from None
    except httpx.HTTPError as exc:
        raise RuntimeError(f"YouTube Analytics API connection failed ({type(exc).__name__})") from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError("YouTube Analytics API returned malformed report data") from None


def _age(row: dict) -> tuple[float | None, str]:
    if row.get("observation_time_basis") == "import_time_export_date_unknown":
        return None, "export_observation_time_unknown"
    published = row.get("published_at")
    observed = row.get("observed_at")
    if not published or not observed:
        return None, "unavailable"
    try:
        collected = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if collected.tzinfo is None:
            return None, "unavailable"
        if len(published) == 10:
            # Midday UTC is explicitly an age estimate with ±24h precision.
            start = datetime.combine(date.fromisoformat(published), time(12), tzinfo=timezone.utc)
            precision = "approximate_day_plus_or_minus_24h"
        else:
            start = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if start.tzinfo is None:
                return None, "unavailable"
            precision = "timestamp"
        hours = (collected - start).total_seconds() / 3600
        return (hours if hours >= 0 else None), precision
    except (ValueError, TypeError):
        return None, "unavailable"


def _wilson(successes: int, trials: int) -> list[float] | None:
    if trials <= 0 or not 0 <= successes <= trials:
        return None
    z = 1.96
    ratio = successes / trials
    denominator = 1 + z * z / trials
    center = (ratio + z * z / (2 * trials)) / denominator
    spread = z * math.sqrt(ratio * (1 - ratio) / trials + z * z / (4 * trials * trials)) / denominator
    return [round(100 * (center - spread), 2), round(100 * (center + spread), 2)]


def _watch_fraction(row: dict) -> tuple[float | None, str | None]:
    metrics = row.get("metrics", {})
    percentage = metrics.get("average_percentage_viewed")
    if percentage is not None:
        return percentage / 100, "reported_average_percentage_viewed"
    average, length = metrics.get("average_view_duration_seconds"), row.get("duration_seconds")
    if average is not None and length is not None and length > 0:
        return average / length, "average_duration_divided_by_length_proxy"
    return None, None


def analyze_performance(rows: list[dict], *, now: datetime | None = None,
                        duration_tolerance: float = 0.2, age_ratio_limit: float = 1.5,
                        minimum_sample: int = 1000) -> dict:
    """Descriptive comparisons only, respecting observation date and cohorts.

    Counts can contain repeated people/exposures. The sample threshold and
    duration/age tolerance are disclosed editorial heuristics, not significance.
    No average watch percentage is used to fabricate repeat counts.
    """
    if not 0 <= duration_tolerance <= 1 or age_ratio_limit < 1 or minimum_sample < 1:
        raise ValueError("Invalid comparison thresholds")
    results = []
    for index, row in enumerate(rows):
        metrics = row.get("metrics", {})
        views, engaged = metrics.get("views"), metrics.get("engaged_views")
        gained, net = metrics.get("subscribers_gained"), metrics.get("subscribers_net")
        age, precision = _age(row)
        viewed, swiped = metrics.get("viewed_count"), metrics.get("swiped_away_count")
        interval = _wilson(viewed, viewed + swiped) if viewed is not None and swiped is not None else None
        sample, basis = (engaged, "engaged_views") if engaged is not None else (views, "views_only_not_engaged")
        uncertainty = "unknown_sample" if sample is None else "small_sample" if sample < minimum_sample else "descriptive_only"
        watch_fraction, watch_basis = _watch_fraction(row)
        results.append({"row_index": index, "video_id": row.get("video_id"), "title": row.get("title"),
                        "observation_time_basis": row.get("observation_time_basis"),
                        "age_hours_at_observation": round(age, 2) if age is not None else None,
                        "age_precision": precision, "sample_size": sample, "sample_basis": basis,
                        "uncertainty": uncertainty,
                        "watch_fraction_for_comparison": watch_fraction, "watch_fraction_basis": watch_basis,
                        "subscribers_gained_per_1000_views": round(gained / views * 1000, 3)
                        if gained is not None and views is not None and views > 0 else None,
                        "net_subscribers_per_1000_views": round(net / views * 1000, 3)
                        if net is not None and views is not None and views > 0 else None,
                        "viewed_rate_percent_from_counts": round(viewed / (viewed + swiped) * 100, 2)
                        if interval else None, "viewed_rate_wilson_95_percent": interval,
                        "interval_caveat": "Binomial approximation requires independent exposures; repeated viewers can violate this" if interval else None,
                        "metrics": dict(metrics), "retention": row.get("retention"), "warnings": list(row.get("warnings", []))})
    comparisons = []
    for i, left in enumerate(rows):
        for j in range(i + 1, len(rows)):
            right = rows[j]
            # Repeated exports of the same video are not independent videos.
            if left.get("video_id") and left.get("video_id") == right.get("video_id"):
                continue
            lengths = [left.get("duration_seconds"), right.get("duration_seconds")]
            ages = [results[i]["age_hours_at_observation"], results[j]["age_hours_at_observation"]]
            if any(value is None or value <= 0 for value in lengths + ages):
                continue
            if max(lengths) / min(lengths) - 1 > duration_tolerance or max(ages) / min(ages) > age_ratio_limit:
                continue
            # Different traffic breakdowns and different report windows confound comparisons.
            if left.get("traffic_source") != right.get("traffic_source"):
                continue
            periods = [(row.get("report_start"), row.get("report_end")) for row in (left, right)]
            if periods[0] != periods[1]:
                continue
            # Known report windows must also be equal in relation to publication,
            # otherwise a one-day-old Short and an old Short's one-day slice differ.
            missing_period = not all(periods[0])
            comparisons.append({"left_row": i, "right_row": j,
                                "status": "preliminary_unknown_report_period" if missing_period else "comparable_descriptive",
                                "age_ratio": round(max(ages) / min(ages), 3),
                                "duration_ratio": round(max(lengths) / min(lengths), 3),
                                "small_sample": any(result["uncertainty"] != "descriptive_only" for result in (results[i], results[j])),
                                "caveat": "Matching duration, age, traffic and report window do not control for topic, distribution or randomness"})
    suggestions = []
    for result, row in zip(results, rows):
        peers = [pair for pair in comparisons if result["row_index"] in (pair["left_row"], pair["right_row"])]
        metrics = result["metrics"]
        if len(peers) < 4 or result["uncertainty"] != "descriptive_only" or any(pair["status"].startswith("preliminary") for pair in peers):
            continue
        ids = {pair["left_row"] if pair["right_row"] == result["row_index"] else pair["right_row"] for pair in peers}
        ids = {index for index in ids if results[index]["uncertainty"] == "descriptive_only"}
        if len(ids) < 4:
            continue
        peer_values = [results[index]["metrics"].get("viewed_percent") for index in ids]
        peer_values = [value for value in peer_values if value is not None]
        viewed_percent = metrics.get("viewed_percent")
        if viewed_percent is not None and len(peer_values) >= 4 and viewed_percent < median(peer_values):
            suggestions.append({"video_id": row.get("video_id"), "hypothesis": "Test a clearer first two seconds and immediate visual subject in a future original video",
                                "evidence": f"Viewed percentage {viewed_percent:g} is below comparable-peer median {median(peer_values):g}",
                                "caveat": "Observational association; this does not prove the hook caused the difference"})
        fraction = result["watch_fraction_for_comparison"]
        # Compare the same measurement basis. An AVD/length proxy is not silently
        # mixed with a provider's reported average percentage definition.
        peer_watch = [results[index]["watch_fraction_for_comparison"] for index in ids
                      if results[index]["watch_fraction_basis"] == result["watch_fraction_basis"]
                      and results[index]["watch_fraction_for_comparison"] is not None]
        if fraction is not None and len(peer_watch) >= 4 and fraction < median(peer_watch):
            evidence = (f"Watch fraction {fraction:.3f} is below comparable-peer median {median(peer_watch):.3f}; "
                        f"basis={result['watch_fraction_basis']}, peers={len(peer_watch)}")
            caveat = "A hypothesis for a future original video; topic and distribution may explain the difference"
            suggestions.extend([
                {"video_id": row.get("video_id"), "area": "script_and_duration",
                 "hypothesis": "Test a shorter script by removing one redundant explanation; preserve the substantive answer and natural speech speed",
                 "evidence": evidence, "caveat": caveat},
                {"video_id": row.get("video_id"), "area": "visual_story_and_pacing",
                 "hypothesis": "Review the middle of the story and test a new meaningful diagram or object action at each causal step; keep the visual style consistent during this comparison",
                 "evidence": evidence,
                 "caveat": caveat + "; an overall average cannot locate the moment viewers left"},
            ])
        retention = result.get("retention")
        if retention and any(point["second"] <= 2 for point in retention):
            suggestions.append({"video_id": row.get("video_id"), "hypothesis": "Review the opening against the supplied retention points before changing pacing",
                                "evidence": "User-supplied retention includes the opening two seconds", "caveat": "Listen and inspect the scene; retention is not a causal experiment"})
    return {"schema_version": 1, "analyzed_at": _iso(_current(now)), "videos": results,
            "comparisons": comparisons, "suggestions": suggestions,
            "comparison_policy": {"duration_tolerance": duration_tolerance, "age_ratio_limit": age_ratio_limit,
                                  "minimum_sample_heuristic": minimum_sample, "minimum_comparable_peers_for_suggestion": 4,
                                  "age_uses": "observation time, never today's age for historical exports"},
            "caveats": ["Views and engaged views remain separate; no absent metric is replaced by zero.",
                        "Thumbnail impressions are not Shorts feed impressions.",
                        "Views minus engaged views is not a replay count; average viewed percentage above 100% is not a replay count.",
                        "Counts may include repeated viewers; small samples and distribution changes create uncertainty.",
                        "Unknown report dates allow preliminary comparisons only; no causal recommendations.",
                        "Publication-day ages have ±24h uncertainty; exact publication times require a timezone.",
                        "No automatic winner, viral prediction, artificial engagement or public publishing."]}
