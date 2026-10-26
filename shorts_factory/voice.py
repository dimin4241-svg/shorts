"""Per-scene narration: OpenAI TTS, local macOS say, or silence."""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from .providers import openai_speech


def make_audio(text: str, output_dir: Path, index: int, *, provider: str, voice: str, language: str) -> Path | None:
    if provider == "none":
        return None
    if provider == "openai":
        path = output_dir / f"audio_{index:02d}.mp3"
        openai_speech(text, path, voice=voice, language=language)
        return path
    if provider == "macos":
        if platform.system() != "Darwin":
            raise RuntimeError("macos voice works only on macOS; use --voice-provider openai or none")
        path = output_dir / f"audio_{index:02d}.aiff"
        cmd = ["say", "-o", str(path)]
        if voice and voice != "alloy":
            cmd += ["-v", voice]
        cmd += [text]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode:
            raise RuntimeError("macOS say failed. Check voices using: say -v '?'")
        return path
    raise ValueError(f"Unknown voice provider: {provider}")
