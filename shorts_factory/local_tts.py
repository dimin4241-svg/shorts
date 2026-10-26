"""Optional, cache-only MLX Qwen3 VoiceDesign runner for Apple Silicon.

This module imports only the standard library until neural synthesis is explicitly
requested. Run it as a standalone file in SHORTS_LOCAL_TTS_PYTHON's environment;
there are no imports from the shorts_factory package in that interpreter.

API source checked 2026-10-26:
https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py
https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit"
DEFAULT_VOICE_DESCRIPTION = (
    "An original adult male native Russian scientific presenter, warm medium-low voice, "
    "clear natural Russian pronunciation and stress, conversational and engaging without shouting. "
    "Speak at a comfortable unhurried pace with meaningful short pauses and varied natural intonation. "
    "No imitation of any celebrity or identifiable real person. No music or sound effects."
)
MAX_TOKENS = 1024
TEMPERATURE = 0.6
RESULT_PREFIX = "SHORTS_LOCAL_TTS_RESULT="
LANGUAGES = {"ru": "Russian", "ru-ru": "Russian", "russian": "Russian",
             "en": "English", "english": "English", "zh": "Chinese", "chinese": "Chinese",
             "ja": "Japanese", "japanese": "Japanese", "ko": "Korean", "korean": "Korean",
             "de": "German", "german": "German", "fr": "French", "french": "French",
             "es": "Spanish", "spanish": "Spanish", "it": "Italian", "italian": "Italian",
             "pt": "Portuguese", "portuguese": "Portuguese"}
DELIVERY = {
    "natural": "Conversational, clear and relaxed, with natural sentence stress and brief pauses.",
    "energetic": "Lively curiosity with restrained emphasis; never rush or shout.",
    "storytelling": "Tell one coherent story, emphasize the surprising contrast and resolve it calmly.",
    "neutral": "Measured, clear and calm, with subtle natural intonation.",
    "calm": "Calm and attentive, with clear consonants and gentle sentence endings.",
    "documentary": "A thoughtful science documentary narrator with conversational warmth.",
}


def _language_name(language: str) -> str:
    value = LANGUAGES.get(str(language).strip().lower())
    if value is None:
        raise ValueError("Unsupported local TTS language; use a Qwen3 language name or ISO code")
    return value


def local_tts_config(*, voice: str = "", delivery: str = "storytelling", language: str = "ru") -> dict:
    """Return nonsecret synthesis settings; voice is an original description.

    VoiceDesign does not accept Yandex/ElevenLabs speaker IDs or reference audio.
    Blank/default/male/narrator select the original Russian science presenter.
    """
    description = str(voice).strip()
    if description.lower() in {"", "default", "male", "russian-male", "narrator"}:
        description = DEFAULT_VOICE_DESCRIPTION
    else:
        if len(description) > 1200:
            raise ValueError("Local voice description must be at most 1200 characters")
        description = "Original voice: " + description + ". No imitation of any celebrity or identifiable real person."
    style = DELIVERY.get(delivery)
    if style is None:
        raise ValueError("Unsupported local delivery: " + ", ".join(DELIVERY))
    model = os.getenv("SHORTS_LOCAL_TTS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if any(character in model for character in ("\n", "\r", "\x00")) or "://" in model:
        raise ValueError("SHORTS_LOCAL_TTS_MODEL must be a cached repository ID or a local model directory")
    return {"provider": "local", "engine": "mlx_audio", "method": "generate_voice_design",
            "model": model, "language": _language_name(language),
            "voice_description": description, "delivery": delivery, "instruct": f"{description} {style}",
            "speed": 1.0, "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE,
            "download_policy": "local_cache_only", "human_audition_required": True}


def _offline_environment() -> dict:
    return {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}


def _resolve_model_path(model_name: str) -> Path:
    local = Path(model_name).expanduser()
    if local.is_dir():
        return local.resolve()
    if local.is_absolute() or model_name.startswith((".", "~")):
        raise FileNotFoundError("Configured local TTS model directory is missing")
    # Explicit cache-only resolution before handing an existing Path to MLX.
    # MLX's general loader otherwise downloads a repository ID automatically.
    from huggingface_hub import snapshot_download
    try:
        snapshot = snapshot_download(repo_id=model_name, local_files_only=True)
    except Exception:
        raise FileNotFoundError("Local TTS model is not cached; complete the explicit model setup first") from None
    resolved = Path(snapshot).resolve()
    if not resolved.is_dir():
        raise FileNotFoundError("Cached local TTS model directory is missing")
    return resolved


@lru_cache(maxsize=1)
def _load_model(model_path: str):
    """Reuse a loaded model when the runner is used repeatedly in one process."""
    from mlx_audio.tts.utils import load_model
    model = load_model(Path(model_path))
    if not callable(getattr(model, "generate_voice_design", None)):
        raise ValueError("Configured model does not implement Qwen3 VoiceDesign")
    return model


def _generate_wav(payload: dict) -> dict:
    """Explicit runner body. Optional numerical/MLX imports happen only here."""
    # Set these before importing Hub/Transformers: some versions cache settings
    # at import time. Runtime never performs first-time model downloads.
    os.environ.update({key: value for key, value in _offline_environment().items()
                       if key in {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"}})
    import numpy as np
    import soundfile as sf

    config = payload["config"]
    model_path = _resolve_model_path(config["model"])
    model = _load_model(str(model_path))
    chunks = []
    sample_rate = None
    generated_tokens = 0
    for result in model.generate_voice_design(
        text=payload["text"], language=config["language"], instruct=config["instruct"],
        max_tokens=MAX_TOKENS, temperature=TEMPERATURE, stream=False, verbose=False,
    ):
        audio = np.asarray(result.audio, dtype=np.float32).squeeze()
        if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
            raise ValueError("Neural TTS returned empty, nonfinite or non-mono audio")
        rate = int(result.sample_rate)
        if not 8000 <= rate <= 192000 or sample_rate is not None and rate != sample_rate:
            raise ValueError("Neural TTS returned an invalid or inconsistent sample rate")
        sample_rate = rate
        token_count = int(getattr(result, "token_count", 0))
        if token_count >= MAX_TOKENS:
            raise ValueError("Neural TTS reached the generation limit; shorten the script and retry")
        generated_tokens += token_count
        chunks.append(audio)
    if not chunks:
        raise ValueError("Neural TTS returned no audio")
    audio = np.concatenate(chunks)
    duration = len(audio) / sample_rate
    peak = float(np.max(np.abs(audio)))
    if not 0.1 <= duration <= 100 or peak < 1e-5:
        raise ValueError("Neural TTS returned silent audio or an invalid duration")
    # Amplitude-only attenuation prevents PCM clipping; no resampling, speed
    # parameter, time stretching, artificial pauses or invented word alignment.
    gain = min(1.0, 0.98 / peak)
    audio = audio * gain
    output = Path(payload["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), audio, sample_rate, subtype="PCM_16", format="WAV")
    return {**config, "resolved_model": str(model_path), "sample_rate": sample_rate,
            "duration_seconds": round(duration, 6), "samples": int(len(audio)),
            "generated_tokens": generated_tokens, "source_peak": peak, "amplitude_gain": gain,
            "audio_format": "WAV_PCM_16", "time_stretched": False}


def _validate_wav(path: Path) -> dict:
    try:
        with wave.open(str(path), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or not 8000 <= rate <= 192000:
                raise ValueError("Local runner output must be mono PCM16 WAV")
            if not 0.1 <= frames / rate <= 100 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError("Local runner output is empty, truncated or has an invalid duration")
            return {"duration_seconds": frames / rate, "sample_rate": rate, "samples": frames}
    except (wave.Error, EOFError, OSError):
        raise ValueError("Local runner did not produce a readable PCM WAV") from None


def synthesize_local(text: str, path: str | Path, *, voice: str = "", delivery: str = "storytelling",
                     language: str = "ru", speed: float = 1.0) -> Path:
    """Synthesize through an explicitly configured optional neural interpreter.

    Requires prepared, fully cached model weights. No fallback voice, package
    installation or download occurs. A .tts.json sidecar records exact settings.
    """
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed != 1:
        raise ValueError("Local neural TTS requires speed=1.0; change the script instead of time-stretching speech")
    if not isinstance(text, str) or not text.strip() or len(text) > 1800 or "\x00" in text:
        raise ValueError("Local TTS text must contain 1–1800 characters")
    output = Path(path).expanduser().resolve()
    if output.suffix.lower() != ".wav":
        raise ValueError("Local neural TTS output must use .wav")
    interpreter = os.getenv("SHORTS_LOCAL_TTS_PYTHON", "").strip()
    if not interpreter:
        raise RuntimeError("Set SHORTS_LOCAL_TTS_PYTHON to the optional prepared MLX voice interpreter")
    executable = Path(interpreter).expanduser()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError("SHORTS_LOCAL_TTS_PYTHON is not an executable interpreter")
    config = local_tts_config(voice=voice, delivery=delivery, language=language)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Only replace a successful result. A failed retry cannot damage an existing
    # audition, and a stale output cannot masquerade as this synthesis result.
    with tempfile.TemporaryDirectory(prefix="shorts-local-tts-", dir=output.parent) as temporary:
        staged = Path(temporary) / "speech.wav"
        payload = {"text": text, "output": str(staged), "config": config}
        try:
            result = subprocess.run(
                [str(executable), str(Path(__file__).resolve()), "--runner"],
                input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
                timeout=900, check=False, env=_offline_environment(),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Local neural TTS timed out; shorten the preview or check available memory") from None
        except OSError:
            raise RuntimeError("Could not start the configured local neural TTS interpreter") from None
        response = None
        for line in result.stdout.splitlines():
            if line.startswith(RESULT_PREFIX):
                try:
                    response = json.loads(line[len(RESULT_PREFIX):])
                except ValueError:
                    pass
        if result.returncode or not isinstance(response, dict) or response.get("status") != "ok":
            code = response.get("error", "runner_failed") if isinstance(response, dict) else "runner_failed"
            messages = {"missing_dependency": "Optional MLX voice runtime is incomplete; finish its explicit setup",
                        "model_not_cached": "Local voice model is not fully cached; finish the explicit model download",
                        "invalid_generation": "Local neural voice produced invalid or incomplete audio; shorten the preview and check the configured model"}
            raise RuntimeError(messages.get(code, "Local neural TTS runner failed; check the optional runtime and model"))
        metadata = response.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("Local neural TTS returned invalid metadata")
        try:
            measured = _validate_wav(staged)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from None
        staged.replace(output)
        metadata.update(measured)
        output.with_suffix(".tts.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _runner_main() -> int:
    try:
        payload = json.load(sys.stdin)
        metadata = _generate_wav(payload)
        response = {"status": "ok", "metadata": metadata}
        status = 0
    except ImportError:
        response, status = {"status": "error", "error": "missing_dependency"}, 1
    except FileNotFoundError:
        response, status = {"status": "error", "error": "model_not_cached"}, 1
    except (ValueError, KeyError, TypeError):
        response, status = {"status": "error", "error": "invalid_generation"}, 1
    except Exception:
        response, status = {"status": "error", "error": "runner_failed"}, 1
    # MLX model-loading messages can precede this line. Emit no exception body or
    # environment dump: model/library errors are not allowed to leak credentials.
    print(RESULT_PREFIX + json.dumps(response, ensure_ascii=False), flush=True)
    return status


if __name__ == "__main__":
    if sys.argv[1:] != ["--runner"]:
        raise SystemExit("This optional local voice module is called by synthesize_local")
    raise SystemExit(_runner_main())
