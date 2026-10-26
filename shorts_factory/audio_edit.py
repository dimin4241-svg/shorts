"""Conservative silence trimming for speech clips before animation timing."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from .render import media_duration, require_ffmpeg

_VERSION = 4
_TAIL_THRESHOLD_DB = -50
_MIN_TAIL_SECONDS = 0.65
_RETAIN_TAIL_SECONDS = 0.25


def prepare_narration(source: Path, folder: Path, index: int) -> tuple[Path, dict]:
    """Trim only a measured long silence at EOF; keep speech and all pauses verbatim.

    A conservative silence probe must find at least 650 ms of terminal quiet at
    -50 dB RMS. In that case only the final silence is shortened to 250 ms. Starts,
    internal pauses and all preceding audio samples are preserved exactly.
    The lossless render-ready WAV is cached against the exact source bytes.
    """
    require_ffmpeg()
    source = Path(source)
    folder = Path(folder) / "prepared_audio"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"scene_{index:02d}.wav"
    manifest_path = output.with_suffix(".json")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if output.is_file() and manifest_path.is_file():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            if cached.get("source_sha256") == digest and cached.get("filter_version") == _VERSION:
                if math.isfinite(media_duration(output)):
                    return output, cached
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    source_duration = media_duration(source)
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(source),
        "-af", f"silencedetect=n={_TAIL_THRESHOLD_DB}dB:d=0.40", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    if probe.returncode:
        raise RuntimeError("Trailing silence detection failed: " + probe.stderr[-1000:])
    starts = [float(value) for value in re.findall(r"silence_start:\s*(-?[0-9.]+)", probe.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*(-?[0-9.]+)", probe.stderr)]
    tail_start = starts[-1] if starts else None
    tail_end = ends[-1] if starts and len(ends) >= len(starts) else source_duration
    detected_tail = max(0.0, source_duration - tail_start) if tail_start is not None and tail_end >= source_duration - .05 else 0.0
    trim_to = source_duration
    if detected_tail >= _MIN_TAIL_SECONDS:
        trim_to = min(source_duration, tail_start + _RETAIN_TAIL_SECONDS)
    filter_chain = f"atrim=end={trim_to:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo"
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
               "-vn", "-af", filter_chain, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(output)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as error:
        output.unlink(missing_ok=True)
        raise RuntimeError("Speech silence trimming exceeded 120 seconds") from error
    if result.returncode or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("Speech silence trimming failed: " + result.stderr[-1000:])
    prepared_duration = media_duration(output)
    if not math.isfinite(prepared_duration) or prepared_duration <= 0 or prepared_duration > source_duration + 0.05:
        output.unlink(missing_ok=True)
        raise RuntimeError("Prepared speech has an invalid duration")
    manifest = {
        "source": source.name,
        "source_sha256": digest,
        "source_duration_seconds": source_duration,
        "prepared_duration_seconds": prepared_duration,
        "removed_seconds": round(max(0.0, source_duration - prepared_duration), 6),
        "detected_trailing_silence_seconds": round(detected_tail, 6),
        "retained_trailing_silence_seconds": round(max(0.0, prepared_duration - tail_start), 6) if trim_to < source_duration and tail_start is not None else None,
        "filter_version": _VERSION,
        "silence_policy": "preserve clip start, all internal pauses and all audio before detected trailing silence; if terminal silence >=650ms at -50dB RMS, retain 250ms",
        "time_stretch": False,
        "speech_content_changed": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output, manifest
