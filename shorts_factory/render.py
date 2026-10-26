"""Native PIL + FFmpeg 9:16 renderer with burnt-in readable captions and SRT."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .story import Story


def require_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            raise RuntimeError(f"{binary} missing; install FFmpeg (macOS: brew install ffmpeg)")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed: {result.stderr[-1200:]}")
    return result.stdout.strip()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    sources = [
        os.environ.get("SHORTS_FONT", ""),
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for src in sources:
        if src and Path(src).is_file():
            try:
                return ImageFont.truetype(src, size)
            except OSError:
                pass
    raise RuntimeError("No Unicode TTF font found. Set SHORTS_FONT to a path to a Cyrillic-compatible .ttf file.")


def _lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            attempt = f"{current} {word}".strip()
            if current and draw.textbbox((0, 0), attempt, font=font)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = attempt
        if current:
            lines.append(current)
    return lines[:max_lines]


def make_frame(image_path: Path, frame_path: Path, title: str, caption: str, index: int, total: int,
               width: int, height: int) -> None:
    with Image.open(image_path) as source:
        canvas = ImageOps.fit(source.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
    surface = canvas.convert("RGBA")
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    paint = ImageDraw.Draw(overlay)
    # Darkening improves text legibility across both busy photos and whiteboard art.
    top_h, bot_y = int(height * .24), int(height * .70)
    for y in range(top_h):
        alpha = max(0, int(175 * (1 - y / top_h)))
        paint.line((0, y, width, y), fill=(5, 9, 18, alpha))
    for y in range(bot_y, height):
        alpha = min(216, int(125 + 100 * (y - bot_y) / max(1, height - bot_y)))
        paint.line((0, y, width, y), fill=(5, 9, 18, alpha))
    surface = Image.alpha_composite(surface, overlay)
    draw = ImageDraw.Draw(surface)
    margin = int(width * .075)
    title_font = _font(max(18, int(width * .049)))
    caption_font = _font(max(25, int(width * .077)))
    small_font = _font(max(12, int(width * .035)))
    for n, line in enumerate(_lines(draw, title, title_font, width - 2 * margin, 2)):
        draw.text((margin, height * .065 + n * int(width * .064)), line, font=title_font, fill="white", stroke_width=1)
    caption_lines = _lines(draw, caption.upper(), caption_font, width - 2 * margin, 3)
    line_height = int(width * .101)
    base_y = int(height * .82) - len(caption_lines) * line_height / 2
    for n, line in enumerate(caption_lines):
        draw.text((margin, int(base_y + n * line_height)), line, font=caption_font, fill="white", stroke_width=2, stroke_fill=(4, 7, 14))
    draw.text((margin, int(height * .943)), f"{index + 1:02d} / {total:02d}", font=small_font, fill=(232, 233, 242))
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    surface.convert("RGB").save(frame_path, "PNG")


def media_duration(path: Path) -> float:
    duration = float(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]))
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Invalid media duration: {path}")
    return duration


def render_segments(frames: list[Path], audios: list[Path | None], out_dir: Path, *, fps: int,
                    silent_seconds: float) -> tuple[Path, list[float]]:
    require_ffmpeg()
    if len(frames) != len(audios) or not frames:
        raise ValueError("Each frame needs exactly one audio entry")
    segments: list[Path] = []
    durations: list[float] = []
    for index, (frame, audio) in enumerate(zip(frames, audios)):
        duration = max(2.5, media_duration(audio) + 0.25) if audio else silent_seconds
        if not 2 <= duration <= 120:
            raise ValueError(f"Unexpected scene duration: {duration}")
        durations.append(duration)
        segment = out_dir / f"segment_{index:02d}.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(fps), "-i", str(frame)]
        if audio:
            cmd += ["-i", str(audio)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        # Avoid shouting, volume jumps and digital clipping between voice clips.
        # Keep the music track disabled by default: narration must stay intelligible.
        if audio:
            cmd += ["-af", f"highpass=f=65,loudnorm=I=-16:TP=-1.5:LRA=9,afade=t=out:st={max(0, duration - .12):.3f}:d=0.12"]
        cmd += ["-t", f"{duration:.3f}", "-vf", f"fade=t=in:st=0:d=0.14,fade=t=out:st={max(0, duration - .2):.3f}:d=0.2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                "-r", str(fps), "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k", "-movflags", "+faststart", str(segment)]
        _run(cmd)
        segments.append(segment)
    playlist = out_dir / "concat.txt"
    # All paths are output-dir controlled, not taken from a model response.
    playlist.write_text("".join(f"file '{segment.resolve()}'\n" for segment in segments), encoding="utf-8")
    output = out_dir / "short.mp4"
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(playlist), "-c", "copy", "-movflags", "+faststart", str(output)])
    return output, durations


def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, rem = divmod(milliseconds, 3600 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_subtitles(story: Story, durations: list[float], dest: Path) -> None:
    total = 0.0
    chunks = []
    for index, (scene, duration) in enumerate(zip(story.scenes, durations), 1):
        chunks.append(f"{index}\n{_timestamp(total)} --> {_timestamp(total + duration)}\n{scene.narration}\n")
        total += duration
    dest.write_text("\n".join(chunks), encoding="utf-8")
