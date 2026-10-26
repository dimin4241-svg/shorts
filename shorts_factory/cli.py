"""CLI to plan, generate and render testable short videos."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .providers import create_story
from .render import make_frame, render_segments, write_subtitles
from .story import demo_story
from .visuals import create_visual
from .voice import make_audio


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return slug or "short"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shorts-factory", description="Generate portrait AI Shorts/Reels with safe API-based images, audio and SRT")
    p.add_argument("--topic", required=True, help="Topic for the short video")
    p.add_argument("--language", default="ru", help="Script and narration language (default ru)")
    p.add_argument("--style", choices=["whiteboard", "cinematic", "infographic", "cartoon"], default="whiteboard")
    p.add_argument("--script-provider", choices=["openai", "anthropic"], default="anthropic")
    p.add_argument("--image-provider", choices=["openai", "claude-svg", "cards"], default="openai")
    p.add_argument("--voice-provider", choices=["openai", "macos", "none"], default="openai")
    p.add_argument("--voice", default="alloy", help="OpenAI voice name or macOS voice name")
    p.add_argument("--variants", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--seconds", type=int, default=30, help="Target video length 12–90 seconds")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--out", default="runs")
    p.add_argument("--demo", action="store_true", help="Entirely local no-API test (demo text, placeholder cards, silence)")
    p.add_argument("--plan-only", action="store_true", help="Show local cost/execution plan; no API calls")
    return p


def execute(args: argparse.Namespace) -> list[Path]:
    if len(args.topic.strip()) > 250 or not args.topic.strip():
        raise ValueError("Topic must be 1–250 characters")
    if not 12 <= args.seconds <= 90:
        raise ValueError("--seconds must be between 12 and 90")
    if args.width < 320 or args.height < 480 or abs(args.width / args.height - 9 / 16) > .01:
        raise ValueError("Use portrait 9:16 and at least 320x480, e.g. 1080x1920")
    if not 12 <= args.fps <= 60:
        raise ValueError("--fps must be between 12 and 60")
    if args.image_provider == "cards" and not args.demo:
        raise ValueError("Placeholder cards are for --demo only")
    if args.plan_only:
        print(json.dumps({
            "topic": args.topic, "variants": args.variants, "style": args.style,
            "script_provider": "demo" if args.demo else args.script_provider,
            "image_provider": "cards" if args.demo else args.image_provider,
            "voice_provider": "none" if args.demo else args.voice_provider,
            "possible_billable_calls": "none" if args.demo else f"up to {args.variants} script + each scene one image/vector + each scene voice (if openai)",
            "output": f"{args.width}x{args.height} H.264 MP4 + SRT + original assets",
        }, ensure_ascii=False, indent=2))
        return []

    # Make the override explicit: --demo NEVER calls an API regardless of other settings.
    script_provider = "demo" if args.demo else args.script_provider
    image_provider = "cards" if args.demo else args.image_provider
    voice_provider = "none" if args.demo else args.voice_provider
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    root = Path(args.out) / f"{stamp}-{_slug(args.topic)}"
    root.mkdir(parents=True, exist_ok=False)
    outputs = []
    for variant in range(1, args.variants + 1):
        print(f"[variant {variant}/{args.variants}] building storyboard...", flush=True)
        story = demo_story(args.topic) if args.demo else create_story(
            args.topic, args.language, args.style, args.seconds, variant, script_provider)
        dest = root / f"v{variant}"
        dest.mkdir(parents=True)
        (dest / "storyboard.json").write_text(json.dumps(story.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        images, frames, audios = [], [], []
        for i, scene in enumerate(story.scenes):
            image = dest / f"image_{i:02d}.png"
            frame = dest / f"frame_{i:02d}.png"
            print(f"  scene {i + 1}/{len(story.scenes)}: image ({image_provider})", flush=True)
            create_visual(image_provider, scene.image_prompt, image, style=args.style)
            make_frame(image, frame, story.title, scene.caption, i, len(story.scenes), args.width, args.height)
            audio = make_audio(scene.narration, dest, i, provider=voice_provider, voice=args.voice, language=args.language)
            images.append(image)
            frames.append(frame)
            audios.append(audio)
        print("  rendering mp4...", flush=True)
        output, durations = render_segments(frames, audios, dest, fps=args.fps, silent_seconds=args.seconds / len(story.scenes))
        write_subtitles(story, durations, dest / "subtitles.srt")
        (dest / "metadata.json").write_text(json.dumps({
            "title": story.title, "description": story.description,
            "topic": args.topic, "duration_seconds": round(sum(durations), 2),
            "script_provider": script_provider, "image_provider": image_provider,
            "voice_provider": voice_provider, "style": args.style,
            "ai_media": not args.demo, "auto_published": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(output)
        print(f"  finished: {output} ({sum(durations):.1f}s)", flush=True)
    return outputs


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    args = parser().parse_args(argv)
    try:
        outputs = execute(args)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if outputs:
        print("\nVideos:")
        for path in outputs:
            print(path.resolve())
    return 0
