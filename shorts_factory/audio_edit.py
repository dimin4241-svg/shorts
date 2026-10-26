"""Conservative silence trimming for speech clips before animation timing."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess

from .render import media_duration, require_ffmpeg

_FILTER = (
    "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo,"
    "silenceremove=start_periods=1:start_duration=0.10:start_threshold=-45dB:"
    "start_silence=0.08:detection=rms:window=0.02,areverse,"
    "silenceremove=stop_periods=-1:stop_duration=0.20:stop_threshold=-40dB:"
    "stop_silence=0.08:detection=rms:window=0.02"
)
_VERSION = 1


def prepare_narration(source: Path, folder: Path, index: int) -> tuple[Path, dict]:
    """Keep short breaths, trim long gaps and edge padding, and preserve the text.

    Long pauses (>200 ms at -40 dB RMS) are shortened to about 300 ms. This changes
    silence only; it does not time-stretch, reorder, or synthesize speech.
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
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
               "-vn", "-af", _FILTER, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(output)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as error:
        output.unlink(missing_ok=True)
        raise RuntimeError("Speech silence trimming exceeded 120 seconds") from error
    if result.returncode or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("Speech silence trimming failed: " + result.stderr[-1000:])
    source_duration = media_duration(source)
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
        "filter_version": _VERSION,
        "silence_policy": "preserve 80ms leading; shorten detected gaps longer than 200ms to about 300ms",
        "time_stretch": False,
        "speech_content_changed": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output, manifest
