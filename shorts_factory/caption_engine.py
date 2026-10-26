"""Readable phrase captions, with honest timing provenance and optional imported alignment.

Imported format: {"source": "transcription" | "forced_alignment" | "manual",
"scenes": [{"words": [{"word": "...", "start": 0.0, "end": 0.4}, ...]}, ...]}.
Times are relative to the start of each scene. Every spoken token must be present,
in order. This validates an import, it does not claim that ASR/alignment ran here.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import ImageDraw

from .render import _font, _timestamp
from .story import Story


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str
    scene: int
    timing: str = "estimated_phrase"
    # Global word times; absent for estimated phrase timing.
    words: tuple[dict, ...] = ()


def display_text(text: str) -> str:
    """Remove speech-only stress markup while preserving ordinary arithmetic plus."""
    return re.sub(r"\+(?=[аеёиоуыэюяАЕЁИОУЫЭЮЯ])", "", text).replace("\u0301", "")


def safe_box(width: int, height: int) -> tuple[int, int, int, int]:
    # Conservative common zone: reserve bottom description and right action rail.
    return round(width * .08), round(height * .10), round(width * .85), round(height * .80)


def _token_key(token: str) -> str:
    return "".join(ch.lower() for ch in display_text(token) if ch.isalnum())


def _groups(words: list[str], *, max_words: int = 4, max_chars: int = 30) -> list[list[str]]:
    join_next = {"в", "во", "на", "по", "под", "над", "за", "из", "из-за", "от", "до", "к", "ко", "с", "со", "у",
                 "о", "об", "обо", "для", "без", "при", "между", "через", "перед", "около", "про", "и", "а", "но", "или", "чтобы"}
    groups: list[list[str]] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and (len(current) >= max_words or len(candidate) > max_chars):
            carry = []
            # Russian prepositions belong with their following noun. Keep every
            # original token while avoiding a dangling 'по' or 'под' on screen.
            while len(current) > 1 and current[-1].lower().strip('«»\"') in join_next:
                carry.insert(0, current.pop())
            groups.append(current)
            current = carry
        current.append(word)
        if word.endswith((".", "!", "?", ";", ":")) and len(current) >= 2:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _validated_words(story: Story, durations: list[float], word_timings: dict[str, Any]) -> list[list[dict]]:
    if not isinstance(word_timings, dict) or word_timings.get("source") not in {"transcription", "forced_alignment", "manual"}:
        raise ValueError("Word timings need source=transcription, forced_alignment, or manual; estimated timings cannot enable karaoke")
    scenes = word_timings.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != len(story.scenes):
        raise ValueError("Word timings must contain exactly one scene entry per storyboard scene")
    validated: list[list[dict]] = []
    for scene_index, (scene, duration, entry) in enumerate(zip(story.scenes, durations, scenes)):
        rows = entry.get("words") if isinstance(entry, dict) else None
        tokens = display_text(scene.narration).split()
        if not isinstance(rows, list) or len(rows) != len(tokens):
            raise ValueError(f"Word timing token count differs in scene {scene_index + 1}")
        previous_end = 0.0
        checked: list[dict] = []
        for token, row in zip(tokens, rows):
            if not isinstance(row, dict) or not isinstance(row.get("word"), str) or _token_key(row["word"]) != _token_key(token):
                raise ValueError(f"Word timing transcript mismatch in scene {scene_index + 1}")
            start, end = row.get("start"), row.get("end")
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise ValueError("Word times must be finite numbers")
            if not math.isfinite(start) or not math.isfinite(end) or start < previous_end - 1e-6 or end <= start or end > duration + 1e-6:
                raise ValueError(f"Invalid or overlapping word timing in scene {scene_index + 1}")
            checked.append({"word": token, "start": float(start), "end": float(end)})
            previous_end = float(end)
        validated.append(checked)
    return validated


def build_caption_cues(story: Story, durations: list[float], word_timings: dict | None = None) -> list[CaptionCue]:
    if not story.scenes or len(story.scenes) != len(durations):
        raise ValueError("Caption durations must match storyboard scenes")
    if any(not math.isfinite(value) or value <= 0 for value in durations):
        raise ValueError("Caption durations must be positive finite numbers")
    imported = _validated_words(story, durations, word_timings) if word_timings is not None else None
    result: list[CaptionCue] = []
    offset = 0.0
    for scene_index, (scene, duration) in enumerate(zip(story.scenes, durations)):
        tokens = display_text(scene.narration).split()
        groups = _groups(tokens)
        cursor = 0
        # Fallback is explicitly estimated. Character weights merely divide phrase
        # slots; they are never exported as word-aligned karaoke timestamps.
        weights = [sum(max(2, len(word.strip('.,!?;:'))) for word in group) for group in groups]
        total_weight = sum(weights) or 1
        elapsed = 0.0
        for group, weight in zip(groups, weights):
            if imported is not None:
                words = imported[scene_index][cursor:cursor + len(group)]
                start, end = words[0]["start"], words[-1]["end"]
                absolute_words = tuple({**word, "start": word["start"] + offset, "end": word["end"] + offset} for word in words)
                timing = "imported_word_timings"
            else:
                start = elapsed
                elapsed += duration * weight / total_weight
                end = min(duration, elapsed)
                absolute_words = ()
                timing = "estimated_phrase"
            result.append(CaptionCue(round(offset + start, 6), round(offset + end, 6), " ".join(group), scene_index,
                                     timing=timing, words=absolute_words))
            cursor += len(group)
        offset += duration
    return result


def text_layout(draw: ImageDraw.ImageDraw, text: str, *, width: int, height: int,
                center_y: float, font_fraction: float = .062, max_lines: int = 2,
                box: tuple[int, int, int, int] | None = None) -> dict:
    """Fit every glyph without truncation. Long words are split only for display."""
    left, top, right, bottom = box or safe_box(width, height)
    available = right - left
    clean = display_text(text)
    size = max(8, round(width * font_fraction))
    min_size = min(size, max(7, round(width * .030)))
    lines: list[str] = []
    while size >= min_size:
        font = _font(size)
        lines = []
        line = ""
        # Split long unspaced words into glyph-fitting fragments before wrapping.
        fragments: list[str] = []
        for token in clean.split():
            part = ""
            for ch in token:
                if part and draw.textbbox((0, 0), part + ch, font=font)[2] > available:
                    fragments.append(part)
                    part = ch
                else:
                    part += ch
            if part:
                fragments.append(part)
        for token in fragments:
            candidate = f"{line} {token}".strip()
            if line and draw.textbbox((0, 0), candidate, font=font)[2] > available:
                lines.append(line)
                line = token
            else:
                line = candidate
        if line:
            lines.append(line)
        line_height = max(1, round(size * 1.24))
        if len(lines) <= max_lines and len(lines) * line_height <= bottom - top:
            break
        size -= 1
    if len(lines) > max_lines or len(lines) * line_height > bottom - top:
        raise ValueError("Caption text cannot fit safe zone; shorten the phrase")
    block_height = len(lines) * line_height
    y = round(max(top, min(center_y - block_height / 2, bottom - block_height)))
    placements = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        # Pillow anchor=lt aligns the visible glyph box (ignores ascender padding).
        x = round((left + right - (bbox[2] - bbox[0])) / 2)
        actual = draw.textbbox((x, y), line, font=font, anchor="lt")
        # Cyrillic glyphs can have a one-pixel negative side bearing at small
        # render sizes. Clamp the measured glyph box itself, not only its
        # nominal advance width, to the safe area used by the quality gate.
        if actual[0] < left:
            x += left - actual[0]
        if actual[2] > right:
            x -= actual[2] - right
        actual = draw.textbbox((x, y), line, font=font, anchor="lt")
        placements.append({"text": line, "x": x, "y": y, "bounds": list(actual)})
        y += line_height
    bounds = [min((p["bounds"][0] for p in placements), default=left),
              min((p["bounds"][1] for p in placements), default=top),
              max((p["bounds"][2] for p in placements), default=left),
              max((p["bounds"][3] for p in placements), default=top)]
    return {"font_size": size, "lines": placements, "bounds": bounds, "safe_box": [left, top, right, bottom],
            "inside_safe_zone": bounds[0] >= left and bounds[1] >= top and bounds[2] <= right and bounds[3] <= bottom}


def _ass_time(seconds: float) -> str:
    centiseconds = round(seconds * 100)
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def write_captions(story: Story, durations: list[float], dest: Path, *, width: int = 1080,
                   height: int = 1920, word_timings: dict | None = None) -> dict:
    """Write SRT, ASS and auditable timing metadata. Returns the JSON manifest."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    cues = build_caption_cues(story, durations, word_timings)
    srt = "\n".join(f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}\n" for index, cue in enumerate(cues, 1))
    (dest / "captions.srt").write_text(srt, encoding="utf-8")
    # Keep the established public artifact name too.
    (dest / "subtitles.srt").write_text(srt, encoding="utf-8")
    font_size = round(width * .062)
    left, _, right, bottom = safe_box(width, height)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{font_size},&H00FFFFFF,&H0055E2FF,&H00150905,&H88150905,-1,0,0,0,100,100,0,0,1,{max(1, round(width*.003))},0,2,{left},{width-right},{height-bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue = []
    for cue in cues:
        if cue.words:
            # Only imported real timings can produce karaoke; preserve gaps with
            # empty syllables instead of falsely painting the next word early.
            pieces = []
            last = cue.start
            for index, word in enumerate(cue.words):
                gap = max(0, round((word["start"] - last) * 100))
                if gap:
                    pieces.append(r"{\k" + str(gap) + "}\u200b")
                span = max(1, round((word["end"] - word["start"]) * 100))
                pieces.append(r"{\kf" + str(span) + "}" + _ass_escape(word["word"]) + (" " if index < len(cue.words) - 1 else ""))
                last = word["end"]
            body = "".join(pieces)
        else:
            body = _ass_escape(cue.text)
        dialogue.append(f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Caption,,0,0,0,,{body}")
    (dest / "captions.ass").write_text(header + "\n".join(dialogue) + "\n", encoding="utf-8")
    manifest = {"version": 1, "timing_mode": "imported_word_timings" if word_timings else "estimated_phrase",
                "timing_method": "imported_word_timings" if word_timings else "estimated_phrase",
                "alignment_ran": False, "imported_source": word_timings.get("source") if word_timings else None,
                "timing_note": "Imported token sequence, finite times and scene bounds validated; import accuracy still depends on its producer." if word_timings else "Phrase slots estimated from narration length. No transcription or forced alignment was performed; word karaoke is disabled.",
                "karaoke": bool(word_timings), "safe_box": list(safe_box(width, height)),
                "cue_count": len(cues), "cues": [{**asdict(cue), "words": list(cue.words)} for cue in cues]}
    (dest / "caption_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
