"""Audio-first production, resumable assets, explicit budgets and honest QA."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .budget import Budget, cached_artifact, save_json
from .providers import create_story
from .story import demo_story, parse_story
from .speech import load_glossary, provider_voice
from .visuals import create_visual
from .voice import make_audio

PAID = {"yandex", "elevenlabs", "openai", "anthropic", "claude-svg", "azure"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "short"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shorts-factory", description="Невидимый мир — original Russian vertical science videos")
    p.add_argument("--topic", default="")
    p.add_argument("--pilot", nargs="?", const="submarine-cables", default="", help="Reviewed local script; default submarine-cables")
    p.add_argument("--storyboard", default="", help="Reviewed storyboard JSON, no script API")
    p.add_argument("--facts-json", default="", help="Reviewed facts array with supporting sources")
    p.add_argument("--language", default="ru")
    p.add_argument("--style", type=str.lower, choices=["whiteboard", "cinematic", "infographic", "cartoon", "explainer"], default="explainer")
    p.add_argument("--script-provider", choices=["openai", "anthropic"], default="anthropic")
    p.add_argument("--image-provider", choices=["openai", "claude-svg", "cards", "procedural"], default=None)
    p.add_argument("--voice-provider", choices=["yandex", "elevenlabs", "openai", "azure", "local", "macos", "none"], default="yandex")
    p.add_argument("--voice", default="")
    p.add_argument("--speech-speed", type=float, default=1.0)
    p.add_argument("--voice-delivery", choices=["natural", "energetic", "storytelling"], default="storytelling")
    p.add_argument("--glossary", default="")
    p.add_argument("--voice-preview", default="")
    p.add_argument("--audio-only", action="store_true", help="Save narration for listening before any imagery")
    p.add_argument("--voice-reviewed", action="store_true", help="Confirm listening to this run's audio before paid imagery (use --resume)")
    p.add_argument("--word-timings", default="", help="Optional verified transcription/forced alignment/manual word timing JSON")
    p.add_argument("--variants", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--seconds", type=int, default=28, help="Pilot duration variants: 20, 28, 40")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--out", default="runs")
    p.add_argument("--resume", default="", help="Existing variant directory; matching artifacts reused by content hash")
    p.add_argument("--demo", action="store_true", help="Entirely local silent demo, all paid providers disabled")
    p.add_argument("--plan-only", "--dry-run", dest="plan_only", action="store_true", help="No calls, files or rendering")
    p.add_argument("--max-cost-usd", type=float, default=0, help="Explicit approximate reservation ceiling; default 0")
    for stage in ("research", "images", "voice", "scripts"):
        p.add_argument(f"--max-{stage}-cost-usd", type=float, default=None)
    p.add_argument("--research", action="store_true", help="Official research sources; key is optional")
    p.add_argument("--google-trends-rss", action="store_true", help="Opt in to public official geo=RU RSS")
    p.add_argument("--google-trends-csv", default=None)
    p.add_argument("--tiktok-csv", default=None)
    p.add_argument("--metrics-csv", default="", help="Analyze a YouTube Studio export")
    p.add_argument("--metrics-observed-at", default="", help="Known export observation timestamp with timezone (ISO 8601); required for age comparisons")
    p.add_argument("--quality-check", default="", help="Rerun only local quality checks on an existing MP4")
    return p


def _limits(args):
    return {s: getattr(args, f"max_{s}_cost_usd") if getattr(args, f"max_{s}_cost_usd") is not None else args.max_cost_usd
            for s in ("research", "images", "voice", "scripts")}


def _models():
    return {k: os.getenv(k, default) for k, default in {
        "OPENAI_TEXT_MODEL": "gpt-4.1-mini", "ANTHROPIC_TEXT_MODEL": "claude-sonnet-4-5",
        "OPENAI_IMAGE_MODEL": "gpt-image-1-mini", "OPENAI_TTS_MODEL": "gpt-4o-mini-tts",
        "ELEVENLABS_TTS_MODEL": "eleven_multilingual_v2",
        "SHORTS_LOCAL_TTS_MODEL": "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit"}.items()}


def execute(args: argparse.Namespace) -> list[Path]:
    from .motion import render_motion
    from .script_engine import catalog, facts_for, load_pilot, plan_for
    from .video_quality_gate import inspect_video

    # Validate even dry runs; NaN must never disable a budget comparison.
    for number in [args.max_cost_usd, *_limits(args).values()]:
        Budget._number(number)
    if args.plan_only:
        planned_script = "curated" if args.pilot or args.storyboard else "demo" if args.demo else args.script_provider
        planned_image = "procedural" if args.demo else args.image_provider or ("procedural" if args.pilot else "openai")
        planned_voice = "none" if args.demo else args.voice_provider
        counts = {"scripts": args.variants if planned_script in PAID and not args.voice_preview else 0,
                  "images": 7 * args.variants if planned_image in PAID and not args.voice_preview and not args.audio_only else 0,
                  "voice": (1 if args.voice_preview else 7 * args.variants) if planned_voice in PAID else 0}
        print(json.dumps({"topic": args.topic, "pilot": args.pilot, "variants": args.variants,
                          "voice_preview": bool(args.voice_preview), "max_cost_usd": args.max_cost_usd,
                          "stage_limits_usd": _limits(args),
                          "possible_billable_calls": counts,
                          "providers": {"scripts": planned_script, "images": planned_image, "voice": planned_voice},
                          "planning_estimates_usd": {"script_request": .08, "image_request": .12, "voice_per_1000_chars": .30},
                          "cost_basis": "Approximate conservative reservations, not quotes or invoices; actual provider charges may differ",
                          "order": ["script + facts", "audio preview", "audio", "voice review before paid imagery", "visuals", "render", "quality gate"],
                          "output": f"{args.width}x{args.height} H.264/AAC MP4 + SRT + ASS"}, ensure_ascii=False, indent=2))
        return []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    out = Path(args.out)
    if args.research:
        from .trend_research import research_trends
        queries = [args.topic] if args.topic else [x.get("topic", x.get("title", "")) for x in catalog()]
        report = research_trends(queries, google_trends_csv=args.google_trends_csv, tiktok_csv=args.tiktok_csv,
                                 include_google_rss=args.google_trends_rss)
        path = out / f"research-{stamp}.json"
        save_json(path, report)
        print(path.resolve())
        return [path]
    if args.metrics_csv:
        from .performance_lab import import_studio_csv, analyze_performance
        observed_at = datetime.fromisoformat(args.metrics_observed_at.replace("Z", "+00:00")) if args.metrics_observed_at else None
        report = analyze_performance(import_studio_csv(Path(args.metrics_csv), observed_at=observed_at))
        path = out / f"performance-{stamp}.json"
        save_json(path, report)
        print(path.resolve())
        return [path]
    if args.quality_check:
        path = Path(args.quality_check)
        facts = json.loads((path.parent / "facts.json").read_text(encoding="utf-8")) if (path.parent / "facts.json").exists() else []
        story = parse_story((path.parent / "storyboard.json").read_text(encoding="utf-8")) if (path.parent / "storyboard.json").exists() else None
        metadata = json.loads((path.parent / "metadata.json").read_text(encoding="utf-8")) if (path.parent / "metadata.json").exists() else {}
        report = inspect_video(path, story=story, facts=facts, require_voice=metadata.get("voice_provider") != "none", target_seconds=metadata.get("target_seconds", args.seconds))
        if not report["automated_pass"]:
            raise RuntimeError(f"Quality gate failed. Repair stages: {report['repair_stages']}; see {path.parent / 'quality_report.json'}")
        return [path.parent / "quality_report.json"]

    if not args.voice_preview and not args.pilot and not args.storyboard and not 1 <= len(args.topic.strip()) <= 250:
        raise ValueError("Provide --topic, --pilot or --storyboard")
    if not .75 <= args.speech_speed <= 1.35:
        raise ValueError("--speech-speed must be between 0.75 and 1.35")
    if not 12 <= args.seconds <= 90:
        raise ValueError("--seconds must be between 12 and 90")
    if args.width < 320 or args.height < 480 or abs(args.width / args.height - 9 / 16) > .002 or args.width % 2 or args.height % 2:
        raise ValueError("Use even 9:16 portrait dimensions, at least 320x480")
    if not 12 <= args.fps <= 60:
        raise ValueError("--fps must be between 12 and 60")
    if args.resume and args.variants != 1:
        raise ValueError("--resume supports one variant at a time")
    if args.voice_preview and (args.demo or args.voice_provider == "none" or not 1 <= len(args.voice_preview.strip()) <= 1000):
        raise ValueError("--voice-preview needs 1–1000 characters and a voice; cannot combine with --demo")
    glossary = load_glossary(args.glossary)
    voice_provider = "none" if args.demo else args.voice_provider
    image_provider = "procedural" if args.demo else args.image_provider or ("procedural" if args.pilot else "openai")
    if image_provider == "cards" and not args.demo:
        raise ValueError("Placeholder cards require --demo")

    if args.voice_preview:
        folder = out / f"voice-preview-{stamp}"
        budget = Budget(folder / "cost_ledger.json", args.max_cost_usd, _limits(args))
        estimate = max(.01, len(args.voice_preview) / 1000 * .30) if voice_provider in PAID else 0
        output = budget.call("voice", voice_provider, estimate, lambda: make_audio(args.voice_preview, folder, 0,
            provider=voice_provider, voice=args.voice, language=args.language, speed=args.speech_speed,
            delivery=args.voice_delivery, glossary=glossary))
        if output is None:
            raise RuntimeError("Voice preview returned no audio")
        print(f"Voice sample: {output.resolve()}")
        return [output]

    root = out / f"{stamp}-{args.pilot or _slug(args.topic)}"
    outputs = []
    ledger_path = root / "cost_ledger.json"
    if args.resume:
        ledger_path = Path(args.resume).parent / "cost_ledger.json"
        if not ledger_path.exists():
            ledger_path = Path(args.resume) / "cost_ledger.json"
    budget = Budget(ledger_path, args.max_cost_usd, _limits(args))
    for variant in range(1, args.variants + 1):
        dest = Path(args.resume) if args.resume else root / f"v{variant}"
        dest.mkdir(parents=True, exist_ok=True)
        settings = {"topic": args.topic, "pilot": args.pilot, "seconds": args.seconds, "variant": variant,
                    "provider": args.script_provider, "style": args.style, "language": args.language,
                    "models": _models(), "demo": args.demo, "storyboard": str(args.storyboard)}
        story_path = dest / "storyboard.json"
        if args.pilot:
            story = load_pilot(args.pilot, args.seconds)
            facts = facts_for(args.pilot)
            plan = plan_for(args.pilot, args.seconds)
            save_json(dest / "editorial_plan.json", plan)
            motion_kinds = [s["motion_kind"] for s in plan["scenes"]]
        elif args.demo:
            story, facts, motion_kinds = demo_story(args.topic), [], None
        elif args.storyboard:
            story, facts, motion_kinds = parse_story(Path(args.storyboard).read_text(encoding="utf-8")), [], None
        else:
            def make_story():
                result = budget.call("scripts", args.script_provider, .08,
                    lambda: create_story(args.topic, args.language, args.style, args.seconds, variant, args.script_provider))
                save_json(story_path, result.as_dict())
                return story_path
            cached_artifact(story_path, settings, make_story)
            story, facts, motion_kinds = parse_story(story_path.read_text(encoding="utf-8")), [], None
        if args.facts_json:
            facts = json.loads(Path(args.facts_json).read_text(encoding="utf-8"))
            if not isinstance(facts, list):
                raise ValueError("--facts-json must contain an array of claim/source records")
        save_json(story_path, story.as_dict())
        save_json(dest / "facts.json", facts)
        save_json(dest / "run_config.json", settings)

        print(f"[variant {variant}] narration before imagery...", flush=True)
        audios = []
        for i, scene in enumerate(story.scenes):
            if voice_provider == "none":
                audios.append(None)
                continue
            extension = "aiff" if voice_provider == "macos" else "wav" if voice_provider == "local" else "mp3"
            path = dest / f"audio_{i:02d}.{extension}"
            voice_settings = {"text": scene.narration, "provider": voice_provider, "voice": provider_voice(voice_provider, args.voice),
                              "language": args.language, "speed": args.speech_speed, "delivery": args.voice_delivery,
                              "glossary": glossary, "models": _models(), "macos_base_wpm": 160,
                              "previous_text": story.scenes[i - 1].narration if i else "",
                              "next_text": story.scenes[i + 1].narration if i + 1 < len(story.scenes) else ""}
            if voice_provider == "local":
                from .local_tts import local_tts_config
                voice_settings["neural_config"] = local_tts_config(voice=args.voice, delivery=args.voice_delivery, language=args.language)
            if voice_provider == "azure":
                voice_settings["azure_region"] = os.getenv("AZURE_SPEECH_REGION", "")
            def synthesize(index=i, narration=scene.narration, config=voice_settings):
                estimate = max(.01, len(narration) / 1000 * .30) if voice_provider in PAID else 0
                return budget.call("voice", voice_provider, estimate,
                    lambda: make_audio(narration, dest, index, provider=voice_provider, voice=args.voice,
                        language=args.language, speed=args.speech_speed, delivery=args.voice_delivery,
                        glossary=glossary, previous_text=config["previous_text"], next_text=config["next_text"]))
            audio = cached_artifact(path, voice_settings, synthesize)
            from .render import media_duration
            if audio:
                media_duration(audio)  # reject malformed provider audio before imagery
            audios.append(audio)
            if i == 0:
                print(f"  hook preview: {audio}", flush=True)
        if args.audio_only:
            outputs.extend(a for a in audios if a)
            print(f"Audio ready; listen and resume: --resume {dest}", flush=True)
            continue
        if image_provider in PAID and not args.voice_reviewed:
            raise RuntimeError(f"Audio saved in {dest}. Listen, then rerun with --resume {dest} --voice-reviewed before paid imagery.")

        images = []
        if image_provider != "procedural":
            for i, scene in enumerate(story.scenes):
                image = dest / f"image_{i:02d}.png"
                def generate(path=image, prompt=scene.image_prompt):
                    budget.call("images", image_provider, .12 if image_provider in PAID else 0,
                                lambda: create_visual(image_provider, prompt, path, style=args.style))
                    return path
                images.append(cached_artifact(image, {"prompt": scene.image_prompt, "style": args.style,
                    "provider": image_provider, "models": _models()}, generate))
        assets = [{"kind": "visual", "provider": image_provider,
                   "rights_basis": "Original procedural artwork created by this MIT project" if image_provider == "procedural" else "Original API-generated artwork; provider commercial terms require account-level review",
                   "media_type": "animated vector primitives" if image_provider == "procedural" else "static illustration with local animation"}]
        if voice_provider != "none":
            assets.append({"kind": "narration", "provider": voice_provider,
                           "rights_basis": "User-selected synthesized voice; check provider/system voice terms before commercial publication"})
        save_json(dest / "provenance.json", {"assets": assets, "music": None, "third_party_footage": False,
                  "visual_disclosure": "Original explanatory animation; not footage of a real cable incident", "synthetic_voice": voice_provider != "none"})
        timings = json.loads(Path(args.word_timings).read_text(encoding="utf-8")) if args.word_timings else None
        output, durations = render_motion(story, audios, dest, width=args.width, height=args.height, fps=args.fps,
            style=args.style, target_seconds=args.seconds, motion_kinds=motion_kinds, images=images or None, word_timings=timings)
        save_json(dest / "metadata.json", {"title": story.title, "description": story.description,
                  "topic": args.topic or args.pilot, "duration_seconds": round(sum(durations), 3), "target_seconds": args.seconds,
                  "script_provider": "curated" if args.pilot or args.storyboard else "demo" if args.demo else args.script_provider,
                  "image_provider": image_provider, "voice_provider": voice_provider, "voice": args.voice or "provider default",
                  "speech_speed": args.speech_speed, "style": args.style, "auto_published": False,
                  "ai_media": image_provider in PAID or voice_provider in PAID, "synthetic_voice": voice_provider != "none"})
        report = inspect_video(output, story=story, facts=facts, require_voice=voice_provider != "none", target_seconds=args.seconds)
        if not report["automated_pass"]:
            raise RuntimeError(f"Quality gate failed; assets preserved at {dest}. Repair only: {report['repair_stages']}")
        outputs.append(output)
        print(f"  finished: {output} ({sum(durations):.1f}s), human listening/viewing still required", flush=True)
    return outputs


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    # Never discover an unrelated parent-directory .env.
    load_dotenv(Path.cwd() / ".env")
    args = parser().parse_args(argv)
    try:
        outputs = execute(args)
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if outputs:
        print("\nGenerated files:")
        for path in outputs:
            print(path.resolve())
    return 0
