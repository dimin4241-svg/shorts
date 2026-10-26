"""API clients: Anthropic text/SVG, OpenAI text, image and voice.

No key is saved to source, logs or output metadata.
"""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path

import httpx

from .story import Story, parse_story, story_prompt


def _key(env: str) -> str:
    key = os.environ.get(env, "").strip()
    if not key:
        raise RuntimeError(f"{env} is missing. Copy .env.example to .env, then set your key.")
    return key


def _request(url: str, *, headers: dict, json: dict, timeout: int = 180) -> httpx.Response:
    try:
        # No redirects: prevents an unexpected redirect taking API authentication elsewhere.
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            response = client.post(url, headers=headers, json=json)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        # Server bodies can be verbose; avoid accidentally logging provider tokens.
        raise RuntimeError(f"Provider returned HTTP {exc.response.status_code} ({exc.request.url.host}). Check model, key, quota and billing.") from None
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Provider connection failed: {type(exc).__name__}") from None


def _openai_headers() -> dict:
    return {"Authorization": f"Bearer {_key('OPENAI_API_KEY')}", "Content-Type": "application/json"}


def _object(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except ValueError:
        raise RuntimeError("Provider returned malformed JSON") from None
    if not isinstance(value, dict):
        raise RuntimeError("Provider returned a non-object JSON response")
    return value


def openai_text(prompt: str) -> str:
    result = _object(_request(
        "https://api.openai.com/v1/responses", headers=_openai_headers(),
        json={"model": os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini"),
              "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
              "max_output_tokens": 2100},
    ))
    blocks = result.get("output")
    if not isinstance(blocks, list):
        raise RuntimeError("OpenAI returned malformed output blocks")
    texts = [part["text"] for block in blocks if isinstance(block, dict) and isinstance(block.get("content"), list)
             for part in block["content"] if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    combined = "\n".join(texts).strip()
    if not combined:
        raise RuntimeError("OpenAI returned no storyboard text")
    return combined


def anthropic_text(prompt: str, *, max_tokens: int = 3000) -> str:
    result = _object(_request(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": _key("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json={"model": os.getenv("ANTHROPIC_TEXT_MODEL", "claude-sonnet-4-5"),
              "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
    ))
    blocks = result.get("content")
    if not isinstance(blocks, list):
        raise RuntimeError("Claude returned malformed content blocks")
    combined = "\n".join(item["text"] for item in blocks if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)).strip()
    if not combined:
        raise RuntimeError("Claude returned no text")
    return combined


def create_story(topic: str, language: str, style: str, seconds: int, variant: int, provider: str) -> Story:
    prompt = story_prompt(topic, language, variant, style, seconds)
    if provider == "openai":
        raw = openai_text(prompt)
    elif provider == "anthropic":
        raw = anthropic_text(prompt)
    else:
        raise ValueError(f"Unknown script provider: {provider}")
    try:
        return parse_story(raw)
    except (ValueError, KeyError) as exc:
        raise RuntimeError(f"Script provider returned invalid JSON: {exc}") from exc


def openai_image(prompt: str, destination: Path, *, style: str) -> None:
    final_prompt = (
        f"Create an ORIGINAL portrait 2:3 visual illustration for a short-form video. "
        f"Style: {style}. {prompt}. Composition optimized for central crop to 9:16. "
        "One compelling central subject, clean depth, strong contrast. "
        "NO TEXT, NO WORDS, NO LETTERS, NO LABELS, NO WATERMARK, NO LOGOS."
    )
    payload = {"model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini"),
               "prompt": final_prompt, "size": "1024x1536", "quality": "medium", "n": 1}
    data = _object(_request("https://api.openai.com/v1/images/generations", headers=_openai_headers(), json=payload))
    images = data.get("data", [])
    if not isinstance(images, list) or not images or not isinstance(images[0], dict) or not isinstance(images[0].get("b64_json"), str):
        raise RuntimeError("Image provider returned no base64 image")
    try:
        destination.write_bytes(base64.b64decode(images[0]["b64_json"], validate=True))
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("Invalid image returned by provider") from exc


def openai_speech(text: str, destination: Path, *, voice: str = "alloy", language: str = "ru") -> None:
    response = _request(
        "https://api.openai.com/v1/audio/speech", headers=_openai_headers(),
        json={"model": os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
              "voice": voice, "response_format": "mp3", "input": text,
              "instructions": f"Speak naturally in {language}, energetic but clear, with brief pauses."},
    )
    if not response.content:
        raise RuntimeError("TTS returned empty audio")
    destination.write_bytes(response.content)
