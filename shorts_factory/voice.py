"""Per-scene narration with Russian-native providers, optional glossary and macOS fallback."""
from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

from .speech import load_glossary, prepare_speech_text, synthesize_audio


def _macos_russian_voice() -> str:
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Cannot list macOS voices (say -v '?')")
    for line in result.stdout.splitlines():
        if re.search(r"\bru[_-]RU\b", line, re.I):
            voice_name = line.split()[0]
            if voice_name:
                return voice_name
    raise RuntimeError("No Russian voice installed on macOS. Install a Russian system voice, or use Yandex/ElevenLabs.")


def make_audio(
    text: str, output_dir: Path, index: int, *,
    provider: str, voice: str, language: str, speed: float = 1.04,
    delivery: str = "energetic", glossary: dict | None = None,
    previous_text: str = "", next_text: str = "",
) -> Path | None:
    if provider == "none":
        return None
    prepared = prepare_speech_text(text, provider, glossary or {})
    if provider == "azure":
        from .azure_speech import synthesize_azure
        path = output_dir / f"audio_{index:02d}.mp3"
        synthesize_azure(prepared, path, voice=voice or "ru-RU-DmitryNeural", language=language, speed=speed, delivery=delivery)
        return path
    if provider == "local":
        from .local_tts import synthesize_local
        path = output_dir / f"audio_{index:02d}.wav"
        return synthesize_local(prepared, path, voice=voice, language=language, speed=speed, delivery=delivery)
    if provider in {"openai", "yandex", "elevenlabs"}:
        path = output_dir / f"audio_{index:02d}.mp3"
        synthesize_audio(
            provider, prepared, path, voice=voice, language=language,
            speed=speed, delivery=delivery,
            previous_text=previous_text, next_text=next_text,
        )
        return path
    if provider == "macos":
        if platform.system() != "Darwin":
            raise RuntimeError("macos voice works only on macOS")
        path = output_dir / f"audio_{index:02d}.aiff"
        selected_voice = voice or _macos_russian_voice()
        # macOS say uses words per minute, not a relative speed multiplier.
        # Russian multi-syllable narration benefits from a measured local baseline.
        # This controls synthesis only; the renderer never time-stretches speech.
        cmd = ["say", "-v", selected_voice, "-r", str(round(160 * speed)), "-o", str(path), prepared]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError("macOS speech failed. List installed voices with: say -v '?'")
        return path
    raise ValueError(f"Unknown voice provider: {provider}")
