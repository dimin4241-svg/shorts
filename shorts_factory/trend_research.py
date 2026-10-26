"""Permission-respecting trend evidence, with explicit missing-data states.

A search sample is neither an exhaustive competition census nor a forecast. No
private endpoints, browser impersonation, downloaded videos or billed models.
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import median
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

import httpx

WINDOWS = {"72h": timedelta(hours=72), "7d": timedelta(days=7), "30d": timedelta(days=30)}
SCORE_WEIGHTS = {"audience_interest": 25, "originality": 20, "visual_story": 20,
                 "retention": 15, "timeliness": 10, "verifiability_accessibility": 10}
YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"


def _now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("Research time must include a timezone")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _date(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def _count(value: object) -> int | None:
    try:
        count = int(str(value))
        return count if count >= 0 else None
    except (ValueError, TypeError):
        return None


def duration_seconds(value: str) -> float | None:
    """Parse the ISO-8601 durations returned by videos.list, including days."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", value or "")
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class YouTubeClient:
    """Official public Data API; capped calls, sanitized failures, no retries.

    A supplied httpx.Client enables deterministic tests. Keys are kept out of
    reports and error messages. Automatic redirect following is disabled.
    """

    def __init__(self, api_key: str, *, client: httpx.Client | None = None,
                 max_requests: int = 90, timeout: float = 20):
        if not api_key.strip():
            raise ValueError("YOUTUBE_API_KEY is missing")
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        self._api_key = api_key.strip()
        self._client = client
        self.max_requests = max_requests
        self.timeout = timeout
        self.request_count = 0

    def _get(self, resource: str, params: dict) -> dict:
        if self.request_count >= self.max_requests:
            raise RuntimeError("YouTube request limit reached; remaining windows are unavailable")
        self.request_count += 1
        try:
            params = {**params, "key": self._api_key}
            if self._client is None:
                with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                    response = client.get(f"{YOUTUBE_BASE}/{resource}", params=params)
            else:
                response = self._client.get(f"{YOUTUBE_BASE}/{resource}", params=params,
                                            timeout=self.timeout, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("Invalid items")
            return payload
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"YouTube Data API HTTP {exc.response.status_code}; check access/quota") from None
        except httpx.HTTPError as exc:
            raise RuntimeError(f"YouTube Data API connection failed ({type(exc).__name__})") from None
        except (ValueError, TypeError):
            raise RuntimeError("YouTube Data API returned malformed data") from None

    def search(self, query: str, window: str, *, now: datetime | None = None,
               max_results: int = 10, region_code: str | None = None) -> dict:
        if window not in WINDOWS:
            raise ValueError(f"Unknown research window: {window}")
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be 1–50")
        current = _now(now)
        after = current - WINDOWS[window]
        params = {"part": "snippet", "type": "video", "q": query, "relevanceLanguage": "ru",
                  "publishedAfter": _iso(after), "publishedBefore": _iso(current),
                  "videoDuration": "short", "order": "viewCount", "maxResults": max_results}
        if region_code:
            if not re.fullmatch(r"[A-Z]{2}", region_code):
                raise ValueError("region_code must be a two-letter uppercase country code")
            params["regionCode"] = region_code
        search = self._get("search", params)
        ids = list(dict.fromkeys(item.get("id", {}).get("videoId") for item in search["items"]
                                 if isinstance(item, dict) and isinstance(item.get("id"), dict)
                                 and isinstance(item["id"].get("videoId"), str)))
        videos = []
        if ids:
            details = self._get("videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids)})
            for item in details["items"]:
                if not isinstance(item, dict) or item.get("id") not in ids:
                    continue
                snippet = item.get("snippet") or {}
                stats = item.get("statistics") or {}
                content = item.get("contentDetails") or {}
                if not all(isinstance(part, dict) for part in (snippet, stats, content)):
                    continue
                published = _date(snippet.get("publishedAt"))
                # Search can return nonmatching/deleted/stale entries; enforce the window locally.
                if published is None or not after <= published <= current:
                    continue
                if snippet.get("liveBroadcastContent", "none") != "none":
                    continue
                length = duration_seconds(content.get("duration", ""))
                if length is not None and length >= 240:
                    continue
                views = _count(stats.get("viewCount"))
                age_hours = (current - published).total_seconds() / 3600
                videos.append({"video_id": item["id"], "title": snippet.get("title"),
                               "channel_id": snippet.get("channelId"), "channel_title": snippet.get("channelTitle"),
                               "url": f"https://www.youtube.com/watch?v={quote(item['id'])}",
                               "published_at": _iso(published), "observed_at": _iso(current),
                               "duration_seconds": length, "views": views,
                               "engaged_views": None, "likes": _count(stats.get("likeCount")),
                               "comments": _count(stats.get("commentCount")),
                               "age_hours": round(age_hours, 3),
                               "lifetime_views_per_hour_proxy": round(views / age_hours, 2)
                               if views is not None and age_hours >= 1 else None,
                               "shorts_classification": "unverified",
                               "duration_candidate_under_3_minutes": length <= 180 if length is not None else None,
                               "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage")})
        counts = [row["views"] for row in videos if row["views"] is not None]
        page_info = search.get("pageInfo")
        page_info = page_info if isinstance(page_info, dict) else {}
        return {"status": "available" if videos else "no_matching_sample", "query": query,
                "window": window, "published_after": _iso(after), "published_before": _iso(current),
                "observed_at": _iso(current), "videos": videos,
                "sample_size": len(videos), "sample_median_lifetime_views": median(counts) if counts else None,
                "approximate_search_results": _count(page_info.get("totalResults")),
                "truncated": bool(search.get("nextPageToken")),
                "source": "YouTube Data API v3", "search_url": "https://www.youtube.com/results?" + urlencode({"search_query": query}),
                "caveats": ["videoDuration=short means under four minutes; it does not identify Shorts.",
                            "Russian relevance is a preference, not verified audience language or age.",
                            "Views are cumulative at collection time; views/age is not measured growth.",
                            "One ranked search page is a biased sample, not a census of competitors.",
                            "Search totals are approximate; results and overlapping windows cannot be added."]}


def _header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lstrip("\ufeff").lower().replace("ё", "е"))


def _csv_rows(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    # Some official Explore downloads include metadata above the blank line.
    rows = list(csv.reader(io.StringIO(text), dialect))
    header_index = next((i for i, row in enumerate(rows) if len(row) >= 2), None)
    if header_index is None:
        raise ValueError("Trend CSV has no table header")
    headers = rows[header_index]
    return [dict(zip(headers, row)) for row in rows[header_index + 1:] if any(cell.strip() for cell in row)]


def import_trend_csv(path: str | Path, *, source: str, observed_at: datetime | None = None,
                     geo: str | None = None) -> dict:
    """Import user-exported public Google Trends or TikTok Creative Center rows.

    TikTok CSV is a documented transcription schema; not every UI provides an
    export button. A country selection never proves Russian-language viewers.
    """
    if source not in {"google_trends", "tiktok_creative_center"}:
        raise ValueError("source must be google_trends or tiktok_creative_center")
    observed = _iso(_now(observed_at))
    records = []
    aliases = {"query", "queries", "trend", "trends", "title", "topic", "hashtag", "keyword", "name",
               "запрос", "поисковый запрос", "тема", "тренды", "тренд", "хештег"}
    time_headers = {"day", "week", "month", "date", "день", "неделя", "месяц", "дата"}
    for raw in _csv_rows(path):
        normalized = {_header(key): str(value).strip() for key, value in raw.items()}
        query = next((value for key, value in normalized.items() if key in aliases and value), None)
        if query:
            records.append({"topic": query, "source": source, "source_url": next(
                (value for key, value in normalized.items() if key in {"url", "link", "ссылка"}), None),
                "observed_at": observed, "geo": geo, "metrics_as_exported": raw,
                "language_audience_verified": False})
        elif source == "google_trends" and any(key in time_headers for key in normalized):
            period = next(value for key, value in normalized.items() if key in time_headers)
            for key, value in raw.items():
                if _header(key) in time_headers or not str(value).strip():
                    continue
                records.append({"topic": key, "source": source, "source_url": None,
                                "observed_at": observed, "geo": geo, "period_as_exported": period,
                                "relative_interest_as_exported": value,
                                "language_audience_verified": False})
    return {"status": "available" if records else "unrecognized_or_empty_export", "source": source,
            "file": Path(path).name, "observed_at": observed, "geo": geo, "records": records,
            "caveats": ["Values and periods are retained as exported, not fabricated or converted to exact views.",
                        "Geography does not identify Russian language or the 16–44 audience.",
                        "Manually exported rows are not a complete census; inspect filters and export dates."]}


def fetch_google_trends_rss(*, geo: str = "RU", now: datetime | None = None,
                            client: httpx.Client | None = None) -> dict:
    if not re.fullmatch(r"[A-Z]{2}", geo):
        raise ValueError("geo must be a two-letter uppercase country code")
    observed = _iso(_now(now))
    url = "https://trends.google.com/trending/rss?" + urlencode({"geo": geo})
    try:
        if client is None:
            with httpx.Client(timeout=20, follow_redirects=False) as session:
                response = session.get(url)
        else:
            response = client.get(url, timeout=20, follow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("RSS exceeded size limit")
        root = ET.fromstring(response.content)
        if root.tag != "rss":
            raise ValueError("Not an RSS feed")
        records = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title")
            if not title:
                continue
            traffic = next((part.text for part in item if part.tag.endswith("}approx_traffic")), None)
            date_raw = item.findtext("pubDate")
            published = None
            if date_raw:
                try:
                    parsed = parsedate_to_datetime(date_raw)
                    published = _iso(parsed.astimezone(timezone.utc)) if parsed.tzinfo else None
                except (ValueError, TypeError, OverflowError):
                    pass
            news_urls = [part.text for part in item.iter() if part.tag.endswith("}news_item_url") and part.text]
            records.append({"topic": title, "url": item.findtext("link"), "published_at": published,
                            "approximate_traffic_bucket": traffic, "news_urls": news_urls,
                            "observed_at": observed, "geo": geo, "language_audience_verified": False})
        return {"status": "available" if records else "empty_or_region_unavailable", "source_url": url,
                "source": "Google Trends RSS", "observed_at": observed, "records": records,
                "caveats": ["Current RSS snapshot only; not a 72h/7d/30d historical series.",
                            "Traffic is a source bucket, not an exact search count.",
                            "Country selection does not establish language or age of the audience."]}
    except httpx.HTTPStatusError as exc:
        reason = f"Official RSS returned HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        reason = f"Official RSS connection failed ({type(exc).__name__})"
    except (ET.ParseError, ValueError):
        reason = "Official RSS returned invalid or oversized XML"
    return {"status": "unavailable", "source": "Google Trends RSS", "source_url": url,
            "observed_at": observed, "records": [], "reason": reason}


def score_idea(components: dict[str, float]) -> dict:
    """Sum editorial points. Every component is required; missing != zero."""
    if set(components) != set(SCORE_WEIGHTS):
        raise ValueError("Supply exactly these score components: " + ", ".join(SCORE_WEIGHTS))
    for key, maximum in SCORE_WEIGHTS.items():
        value = components[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f"{key} must be a finite number from 0 to {maximum}")
    return {"score": round(sum(components.values()), 1), "maximum": 100,
            "components": dict(components), "component_maxima": dict(SCORE_WEIGHTS),
            "meaning": "Editorial hypothesis priority, not predicted views or a virality probability"}


def compare_youtube_snapshots(previous: dict, current: dict) -> dict:
    """Measure counter changes only for videos observed in both saved reports.

    Unlike views/age, this is an observed interval. It still does not identify
    Russian viewers, independent viewers, Shorts distribution or causal lift.
    """
    def index(report: dict) -> dict:
        indexed = {}
        for sample in report.get("youtube", []):
            for video in sample.get("videos", []):
                identifier = video.get("video_id")
                observed = _date(video.get("observed_at"))
                if identifier and observed and video.get("views") is not None:
                    if identifier not in indexed or observed > _date(indexed[identifier]["observed_at"]):
                        indexed[identifier] = video
        return indexed

    before, after = index(previous), index(current)
    changes = []
    for identifier in sorted(before.keys() & after.keys()):
        first, last = before[identifier], after[identifier]
        hours = (_date(last["observed_at"]) - _date(first["observed_at"])).total_seconds() / 3600
        if hours <= 0:
            continue
        delta = last["views"] - first["views"]
        changes.append({"video_id": identifier, "from_observed_at": first["observed_at"],
                        "to_observed_at": last["observed_at"], "interval_hours": round(hours, 3),
                        "views_counter_change": delta,
                        "observed_views_per_hour": round(delta / hours, 2) if delta >= 0 else None,
                        "status": "measured_counter_change" if delta >= 0 else "counter_revision_not_growth"})
    return {"status": "available" if changes else "insufficient_overlapping_observations",
            "videos": changes,
            "caveats": ["Same video IDs only; changing ranked search samples introduce selection bias.",
                        "A counter change is not unique viewers, engaged views or verified Russian-audience growth.",
                        "Negative changes are retained as counter revisions, never reported as negative growth."]}


def research_trends(queries: list[str], *, windows: tuple[str, ...] = ("72h", "7d", "30d"),
                    api_key: str | None = None, now: datetime | None = None,
                    youtube_client: YouTubeClient | None = None,
                    google_trends_csv: str | Path | None = None,
                    tiktok_csv: str | Path | None = None, google_trends_geo: str = "RU",
                    include_google_rss: bool = False, max_results: int = 10,
                    max_requests: int = 90, region_code: str | None = None) -> dict:
    """Collect bounded evidence. An explicit key/constructed client enables API.

    Passing api_key='' disables environment lookup. RSS requires explicit opt-in.
    Missing credentials produce a useful report without any network requests.
    """
    current = _now(now)
    if any(window not in WINDOWS for window in windows):
        raise ValueError("Allowed windows: 72h, 7d, 30d")
    if not queries or len(queries) > 50 or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise ValueError("Supply 1–50 nonempty queries")
    key = os.getenv("YOUTUBE_API_KEY", "") if api_key is None else api_key
    youtube = youtube_client or (YouTubeClient(key, max_requests=max_requests) if key.strip() else None)
    evidence = []
    for query in dict.fromkeys(q.strip() for q in queries):
        for window in dict.fromkeys(windows):
            if youtube is None:
                evidence.append({"query": query, "window": window, "status": "unavailable",
                                 "observed_at": _iso(current), "videos": [], "sample_size": None,
                                 "reason": "YOUTUBE_API_KEY not configured; popularity and competition unmeasured"})
                continue
            try:
                evidence.append(youtube.search(query, window, now=current, max_results=max_results,
                                               region_code=region_code))
            except RuntimeError as exc:
                evidence.append({"query": query, "window": window, "status": "unavailable",
                                 "observed_at": _iso(current), "videos": [], "sample_size": None, "reason": str(exc)})
    google = (import_trend_csv(google_trends_csv, source="google_trends", observed_at=current, geo=google_trends_geo)
              if google_trends_csv else fetch_google_trends_rss(geo=google_trends_geo, now=current)
              if include_google_rss else {"status": "not_requested", "records": [],
                                          "reason": "Supply an official CSV export or opt in to the public RSS"})
    tiktok = (import_trend_csv(tiktok_csv, source="tiktok_creative_center", observed_at=current)
              if tiktok_csv else {"status": "manual_data_required", "records": [],
                                  "source_url": "https://ads.tiktok.com/business/creativecenter/",
                                  "reason": "Use available official UI; export or transcribe visible rows. No private scraping."})
    return {"schema_version": 1, "researched_at": _iso(current), "target_language": "ru",
            "target_age": "16–44 (editorial intent; audience demographics are not measured)",
            "youtube": evidence, "google_trends": google, "tiktok_creative_center": tiktok,
            "youtube_requests": youtube.request_count if youtube else 0,
            "score_weights": dict(SCORE_WEIGHTS),
            "interpretation": "Evidence informs hypotheses. No invented metrics, demographic verification or reach promises."}
