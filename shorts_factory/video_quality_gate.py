"""Measurable media checks with explicit limits on automated editorial judgment."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from .budget import save_json


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


def inspect_video(path: Path, *, story=None, facts=None, require_voice=True,
                  target_seconds=28, report_path: Path | None = None) -> dict:
    checks = []

    def check(name, passed, details, *, critical=True, repair="render"):
        checks.append({"name": name, "status": "pass" if passed else ("fail" if critical else "review"),
                       "critical": critical, "details": details, "repair_stage": repair})

    check("file", path.is_file() and path.stat().st_size > 0, "MP4 must exist and contain bytes")
    info = {}
    if path.is_file():
        result = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
        try:
            info = json.loads(result.stdout) if result.returncode == 0 else {}
        except ValueError:
            pass
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float(info.get("format", {}).get("duration", 0))
    check("streams", bool(video and audio), "Real decodable video and audio streams are required")
    width, height = video.get("width", 0), video.get("height", 0)
    check("portrait", bool(height and abs(width / height - 9 / 16) < .002), {"width": width, "height": height})
    check("codec", video.get("codec_name") == "h264" and audio.get("codec_name") == "aac", "H.264 / AAC")
    check("duration", math.isfinite(duration) and 12 <= duration <= 90, {"actual_seconds": duration, "target_seconds": target_seconds})
    check("target_duration", abs(duration - target_seconds) <= max(3, target_seconds * .18),
          "Target is editorial guidance; never speed up narration to fit", critical=False, repair="script_or_voice")
    analysis = ""
    if video and audio:
        result = _run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
                       "-vf", "blackdetect=d=0.12:pix_th=0.06:pic_th=0.98,signalstats,metadata=mode=print:key=lavfi.signalstats.YMIN,metadata=mode=print:key=lavfi.signalstats.YMAX",
                       "-af", "silencedetect=n=-40dB:d=1.2,loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json",
                       "-f", "null", "-"])
        analysis = result.stderr
        check("decode", result.returncode == 0, "Decode every frame and audio sample")
        black = [float(x) for x in re.findall(r"black_duration:([\d.]+)", analysis)]
        check("black_frames", not black, {"black_intervals_seconds": black, "threshold": "98% pixels black for >=0.12s"})
        minima = [float(x) for x in re.findall(r"lavfi.signalstats.YMIN=([\d.]+)", analysis)]
        maxima = [float(x) for x in re.findall(r"lavfi.signalstats.YMAX=([\d.]+)", analysis)]
        flat = sum(high - low < 6 for low, high in zip(minima, maxima))
        check("empty_frames", bool(minima) and len(minima) == len(maxima) and not flat,
              {"frames_measured": len(minima), "near_uniform_frames": flat, "luma_span_threshold": 6,
               "limitation": "Spatial contrast detects flat frames, not whether an illustration is meaningful"})
        silences = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", analysis)]
        # An open silence interval at EOF has no duration in some FFmpeg versions.
        starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", analysis)]
        ends = re.findall(r"silence_end:", analysis)
        if len(starts) > len(ends) and duration:
            silences.append(duration - starts[-1])
        if require_voice:
            check("excessive_silence", not any(x > 1.5 for x in silences), {"intervals_seconds": silences}, repair="voice")
            blocks = re.findall(r'\{\s*"input_i".*?\}', analysis, flags=re.S)
            loudness = json.loads(blocks[-1]) if blocks else {}
            integrated = float(loudness.get("input_i", "-inf"))
            peak = float(loudness.get("input_tp", "inf"))
            check("loudness", math.isfinite(integrated) and -18 <= integrated <= -14,
                  {"integrated_lufs": integrated if math.isfinite(integrated) else None, "target_lufs": -16}, repair="audio_master")
            check("clipping", math.isfinite(peak) and peak <= -1.0,
                  {"true_peak_dbtp": peak if math.isfinite(peak) else None, "target_dbtp": -1.5, "aac_tolerance_db": .5}, repair="audio_master")
        else:
            check("demo_silence", False, "Intentional silent demo; no narration quality claim", critical=False, repair="voice")

    folder = path.parent
    manifest_file = folder / "caption_manifest.json"
    if manifest_file.exists():
        captions = json.loads(manifest_file.read_text(encoding="utf-8"))
        cues = captions.get("cues", [])
        check("captions_present", bool(cues), "Phrase cues required", repair="captions")
        previous = 0.0
        timing_ok = True
        for cue in cues:
            begin, end = float(cue["start"]), float(cue["end"])
            timing_ok &= math.isfinite(begin) and math.isfinite(end) and begin >= previous - .001 and begin < end <= duration + .1
            previous = end
        check("caption_intervals", timing_ok, "Ordered non-overlapping cues inside audio duration", repair="captions")
        if story:
            normalize = lambda x: re.sub(r"[^\wё]+", "", x.casefold())
            expected = normalize(" ".join(s.narration for s in story.scenes))
            actual = normalize(" ".join(cue["text"] for cue in cues))
            check("caption_text", expected == actual, "Caption words match narration script", repair="captions")
        method = captions.get("timing_method", captions.get("method", "unknown"))
        check("caption_alignment", False, {"method": method, "note": "Text match and intervals do not establish acoustic synchronization; listen to verify"}, critical=False, repair="captions")
    else:
        check("captions_present", False, "caption_manifest.json missing", repair="captions")

    geometry_path = folder / "geometry.json"
    if geometry_path.exists():
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        # Renderer supplies bounds for every controlled text overlay.
        check("text_geometry", geometry.get("all_text_within_safe_zone", False), geometry, repair="captions")
    else:
        check("text_geometry", False, "No text geometry evidence", repair="captions")
    timeline_path = folder / "timeline.json"
    if timeline_path.exists():
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        previous = 0.0
        rows = timeline.get("scenes", [])
        continuous = bool(rows)
        for row in rows:
            start, end = float(row.get("start", -1)), float(row.get("end", -1))
            continuous &= math.isfinite(start) and math.isfinite(end) and abs(start - previous) < .01 and end > start
            previous = end
        continuous &= abs(previous - duration) <= .2 and (story is None or len(rows) == len(story.scenes))
        check("scene_timeline", continuous, "No timeline gaps or overlaps; logical continuity still requires viewing")
    else:
        check("scene_timeline", False, "Timeline missing")
    sourced = isinstance(facts, list) and bool(facts) and all(isinstance(item, dict) and (item.get("sources") or item.get("source_urls")) for item in facts)
    check("sources", sourced, "Every recorded claim needs supporting source URLs", critical=require_voice, repair="facts")
    check("fact_accuracy", False, "Sources are recorded; whether each claim follows from them requires editorial review", critical=False, repair="facts")
    provenance_path = folder / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
    check("media_provenance", bool(provenance.get("assets")) and all(a.get("rights_basis") for a in provenance.get("assets", [])),
          "Each asset requires rights provenance", repair="assets")
    for name, detail in (("speech_intelligibility", "Human listening: Russian stresses, ё, natural pauses, no clicks"),
                         ("editorial_continuity", "Human viewing: correct explanation, useful visual changes and satisfying answer"),
                         ("visual_objects", "Human viewing: captions do not hide the main subject; illustrations are not incident footage")):
        check(name, False, detail, critical=False, repair="editorial")
    failed = [c for c in checks if c["status"] == "fail"]
    report = {"status": "fail" if failed else "needs_human_review", "automated_pass": not failed,
              "publication_approved": False, "checks": checks,
              "repair_stages": sorted({c["repair_stage"] for c in failed}),
              "limitations": "No automatic proof of factual truth, voice quality or audience interest."}
    save_json(report_path or folder / "quality_report.json", report)
    return report
