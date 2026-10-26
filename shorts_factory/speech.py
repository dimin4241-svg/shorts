"""Russian-first TTS integrations, pronunciation correction and voice previews.

Third-party providers are opt-in; credentials live only in local env files.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx


DEFAULT_VOICES = {
    "yandex": "ermil",
    "openai": "cedar",
    "elevenlabs": "",
    "macos": "",
}

RU_STYLE_INSTRUCTIONS = {
    "natural": "Говори на современном русском языке, без иностранного акцента. Точно и естественно произноси слова. Интонация живого рассказчика, плавная, но не монотонная; не переигрывай.",
    "energetic": "Говори на естественном русском языке, как увлечённый ведущий короткого познавательного видео. Начни уверенно и заинтересованно, делай смысловые акценты, не кричи и не тараторь.",
    "storytelling": "Говори на естественном русском языке, как хороший рассказчик: с интригой, короткими осмысленными паузами и выразительной развязкой. Не растягивай гласные.",
}


def provider_voice(provider: str, requested: str) -> str:
    if requested:
        return requested
    if provider == "azure":
        return "ru-RU-DmitryNeural"
    if provider == "elevenlabs":
        name = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        if not name:
            raise RuntimeError("ELEVENLABS_VOICE_ID required: choose a Russian voice from ElevenLabs Voices")
        return name
    return DEFAULT_VOICES.get(provider, "")


def _secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} missing. Put it in a local .env (never commit it).")
    return value


def _post(url: str, *, headers: dict[str, str], payload: dict[str, Any], form: bool = False) -> bytes:
    try:
        with httpx.Client(follow_redirects=False, timeout=180) as client:
            args: dict[str, Any] = {"headers": headers}
            args["data" if form else "json"] = payload
            result = client.post(url, **args)
            result.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Avoid logging a provider response body or a request URL with token parameters.
        raise RuntimeError(f"Speech service returned HTTP {exc.response.status_code}; check account quota and voice settings") from None
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Speech service network error: {type(exc).__name__}") from None
    if not result.content:
        raise RuntimeError("Speech service returned empty audio")
    if len(result.content) > 20_000_000:
        raise RuntimeError("Speech audio response exceeds 20 MB")
    return result.content


def load_glossary(path: str | None) -> dict[str, dict[str, str]]:
    """Explicit, reviewed word replacements. No blind semantic correction."""
    if not path:
        return {"replacements": {}, "yandex_stress": {}}
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"replacements", "yandex_stress"}:
        raise ValueError("Glossary supports only replacements and yandex_stress objects")
    result: dict[str, dict[str, str]] = {}
    for section in ("replacements", "yandex_stress"):
        mapping = data.get(section, {})
        if not isinstance(mapping, dict) or len(mapping) > 200:
            raise ValueError(f"{section} must be an object of at most 200 items")
        result[section] = {}
        for key, value in mapping.items():
            if (not isinstance(key, str) or not isinstance(value, str) or
                not 1 <= len(key) <= 60 or not 1 <= len(value) <= 160 or
                "\n" in key or "\n" in value):
                raise ValueError(f"Invalid {section} entry")
            if section == "yandex_stress":
                # Yandex uses '+' immediately before the stressed Russian vowel.
                if (value.replace("+", "").casefold() != key.casefold()
                        or value.count("+") != 1
                        or not re.search(r"\+[аеёиоуыэюяАЕЁИОУЫЭЮЯ]", value)
                        or not re.fullmatch(r"[А-Яа-яЁё+\-]+", value)):
                    raise ValueError("yandex_stress values must be the same Russian word with one + before a vowel")
            result[section][key] = value
    return result


def _replace_whole_words(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    # Case-sensitive and longest-first to avoid unexpectedly changing a homograph.
    expressions = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(key) for key in expressions) + r")(?!\w)")
    return pattern.sub(lambda match: mapping[match.group(0)], text)


def prepare_speech_text(text: str, provider: str, glossary: dict[str, dict[str, str]]) -> str:
    import unicodedata
    sanitized = unicodedata.normalize("NFC", text).strip()
    if not sanitized or len(sanitized) > 2500:
        raise ValueError("Speech text must be between 1 and 2500 characters")
    sanitized = _replace_whole_words(sanitized, glossary.get("replacements", {}))
    if provider == "yandex":
        sanitized = _replace_whole_words(sanitized, glossary.get("yandex_stress", {}))
    return sanitized


def synthesize_audio(
    provider: str, text: str, destination: Path, *,
    voice: str, language: str, speed: float, delivery: str,
    previous_text: str = "", next_text: str = "",
) -> None:
    if not 0.75 <= speed <= 1.35:
        raise ValueError("Speech speed should be between 0.75 and 1.35")
    voice = provider_voice(provider, voice)
    if provider == "yandex":
        if not language.lower().startswith("ru"):
            raise ValueError("Yandex SpeechKit v1 configuration is Russian only")
        body = {"text": text, "lang": "ru-RU", "voice": voice, "speed": f"{speed:.2f}", "format": "mp3"}
        # SpeechKit's emotion support is voice-specific. Avoid invalid roles.
        if delivery == "energetic" and voice in {"ermil", "jane", "zahar"}:
            body["emotion"] = "good"
        content = _post(
            "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
            headers={"Authorization": f"Api-Key {_secret('YANDEX_API_KEY')}"},
            payload=body,
            form=True,
        )
    elif provider == "elevenlabs":
        import urllib.parse
        import re as regex
        if not regex.fullmatch(r"[A-Za-z0-9_-]{3,100}", voice):
            raise ValueError("Invalid ELEVENLABS_VOICE_ID")
        settings = {"stability": 0.52 if delivery != "natural" else 0.64,
                    "similarity_boost": 0.78, "style": 0.12 if delivery == "energetic" else 0.0,
                    "use_speaker_boost": True, "speed": speed}
        body: dict[str, Any] = {
            "text": text, "model_id": os.getenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2"),
            "voice_settings": settings,
            "apply_text_normalization": "auto",
        }
        if previous_text:
            body["previous_text"] = previous_text[:400]
        if next_text:
            body["next_text"] = next_text[:400]
        content = _post(
            "https://api.elevenlabs.io/v1/text-to-speech/" + urllib.parse.quote(voice, safe=""),
            headers={"xi-api-key": _secret("ELEVENLABS_API_KEY"), "Content-Type": "application/json"},
            payload=body,
        )
    elif provider == "openai":
        guidance = RU_STYLE_INSTRUCTIONS.get(delivery, RU_STYLE_INSTRUCTIONS["natural"]) if language.lower().startswith("ru") else "Natural clear narration, with meaningful pauses."
        content = _post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {_secret('OPENAI_API_KEY')}", "Content-Type": "application/json"},
            payload={"model": os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                     "voice": voice, "response_format": "mp3", "input": text,
                     "instructions": guidance, "speed": speed},
        )
    else:
        raise ValueError(f"Unsupported paid TTS provider: {provider}")
    destination.write_bytes(content)
