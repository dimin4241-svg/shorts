"""Optional official Azure Speech REST synthesis for Russian neural voices.

Contract checked 2026-10-26 against Microsoft's REST and SSML documentation:
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice

Credentials come from the process environment populated by the local .env loader.
This module makes one explicit request, never retries or uses the Edge endpoint.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import unicodedata
from xml.sax.saxutils import escape

import httpx

# Public-cloud regional REST endpoints documented by Microsoft. Sovereign clouds
# have different hostnames and are intentionally outside this small integration.
_REGIONS = frozenset("""
australiaeast brazilsouth canadacentral canadaeast centralus eastasia eastus
eastus2 francecentral germanywestcentral centralindia italynorth japaneast
japanwest koreacentral northcentralus northeurope norwayeast qatarcentral
southafricanorth southcentralus southeastasia swedencentral switzerlandnorth
switzerlandwest uaenorth uksouth ukwest westcentralus westeurope westus westus2 westus3
""".split())
_VOICES = frozenset({"ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural"})
_OUTPUT_FORMAT = "audio-48khz-192kbitrate-mono-mp3"
_MAX_AUDIO_BYTES = 20_000_000


def _secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} missing. Configure it in local .env; never commit credentials.")
    return value


def _is_mp3(content: bytes) -> bool:
    """Reject JSON/HTML disguised as audio without claiming decoder-level QA."""
    offset = 0
    if content.startswith(b"ID3"):
        if len(content) < 10 or any(value & 0x80 for value in content[6:10]):
            return False
        tag_size = sum(value << shift for value, shift in zip(content[6:10], (21, 14, 7, 0)))
        offset = 10 + tag_size + (10 if content[5] & 0x10 else 0)
    header = content[offset:offset + 4]
    return (len(header) == 4 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
            and header[1] & 0x18 != 0x08 and header[1] & 0x06 == 0x02
            and 0 < header[2] >> 4 < 15 and header[2] & 0x0C != 0x0C)


def synthesize_azure(
    text: str, destination: Path, *, voice: str = "ru-RU-DmitryNeural",
    language: str = "ru", speed: float = 1.0, delivery: str = "storytelling",
) -> None:
    """Write MP3 after a single validated Azure request.

    ``delivery`` is accepted for the pipeline's shared interface. These voices use
    their neutral style: no undocumented emotion, role, pitch or forced duration
    is inferred. ``speed=1.04`` becomes SSML ``rate="+4.00%"``.
    """
    if voice not in _VOICES:
        raise ValueError("Azure voice must be a supported ru-RU neural voice (Dmitry, Svetlana or Dariya)")
    if not isinstance(language, str) or language.lower() not in {"ru", "ru-ru"}:
        raise ValueError("This Azure Speech configuration supports Russian (ru or ru-RU) only")
    if (isinstance(speed, bool) or not isinstance(speed, (int, float))
            or not math.isfinite(speed) or not 0.75 <= speed <= 1.35):
        raise ValueError("Speech speed should be between 0.75 and 1.35")
    if delivery not in {"natural", "energetic", "storytelling"}:
        raise ValueError("Unknown delivery; use natural, energetic or storytelling")
    if not isinstance(text, str):
        raise ValueError("Speech text must be between 1 and 2500 characters")
    prepared = unicodedata.normalize("NFC", text).strip()
    if (not 1 <= len(prepared) <= 2500 or any(
            ord(char) < 32 and char not in "\t\n\r" or 0xD800 <= ord(char) <= 0xDFFF
            or ord(char) in {0xFFFE, 0xFFFF} for char in prepared)):
        raise ValueError("Speech text must contain 1–2500 valid XML characters")
    region = _secret("AZURE_SPEECH_REGION").lower()
    if region not in _REGIONS:
        raise ValueError("Invalid AZURE_SPEECH_REGION; use a documented public-cloud Speech region")
    rate = f"{(speed - 1) * 100:+.2f}%"
    ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ru-RU">'
            f'<voice name="{voice}"><prosody rate="{rate}">{escape(prepared)}</prosody></voice></speak>')
    headers = {"Ocp-Apim-Subscription-Key": _secret("AZURE_SPEECH_KEY"),
               "Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
               "User-Agent": "shorts-factory"}
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    try:
        with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(120.0, connect=15.0),
                          transport=httpx.HTTPTransport(retries=0)) as client:
            response = client.post(url, headers=headers, content=ssml.encode("utf-8"))
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Azure Speech returned HTTP {exc.response.status_code}; check region, quota and voice") from None
    except httpx.HTTPError as exc:
        # Provider bodies, headers and exception strings can contain credentials.
        raise RuntimeError(f"Azure Speech network error: {type(exc).__name__}") from None
    content = response.content
    if not content or len(content) > _MAX_AUDIO_BYTES:
        raise RuntimeError("Azure Speech returned empty or oversized audio")
    if not response.headers.get("Content-Type", "").lower().startswith("audio/") or not _is_mp3(content):
        raise RuntimeError("Azure Speech returned invalid MP3 audio")
    Path(destination).write_bytes(content)
