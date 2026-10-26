"""Storyboard schema, defensive JSON parsing and prompts."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Scene:
    narration: str
    caption: str
    image_prompt: str


@dataclass(frozen=True)
class Story:
    title: str
    description: str
    scenes: list[Scene]

    def as_dict(self) -> dict:
        return asdict(self)


def parse_story(raw: str) -> Story:
    """Validate provider output; never treat LLM text as executable commands."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Storyboard must be a JSON object")
    title = _field(data, "title", 120)
    description = _field(data, "description", 600)
    rows = data.get("scenes")
    if not isinstance(rows, list) or not 3 <= len(rows) <= 7:
        raise ValueError("Storyboard must contain 3–7 scenes")
    scenes = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("A scene must be an object")
        scenes.append(Scene(
            narration=_field(row, "narration", 400),
            caption=_field(row, "caption", 100),
            image_prompt=_field(row, "image_prompt", 650),
        ))
    return Story(title, description, scenes)


def _field(data: dict, key: str, limit: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing text field: {key}")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"Field {key} exceeds {limit} characters")
    return value


def story_prompt(topic: str, language: str, variant: int, style: str, target_seconds: int) -> str:
    if not 12 <= target_seconds <= 90:
        raise ValueError("target_seconds must be 12–90")
    return f"""You are an expert short-form video producer. Create an original, FACT-CHECKABLE short video storyboard.
TOPIC: {topic}
LANGUAGE for title, description, narration and captions: {language}
VISUAL STYLE: {style}
VARIANT: {variant}. Produce a meaningfully distinct opening angle, not a near-copy of other variants.
TARGET DURATION: around {target_seconds} seconds. 3–6 scenes. Target ~2.1–2.5 spoken words per second for the WHOLE VIDEO. Each scene has one short spoken sentence of about 7–15 words; prefer fewer words to rushing. Avoid filler.
RULES: First scene starts immediately with a precise curiosity hook in the first 2 seconds. Every next scene delivers a clear piece of the answer. Finish with a satisfying specific conclusion. Avoid long introductions, filler, unverified sensationalism and fake statistics. Use simple everyday language. Don't invent news/facts; if a claim requires current evidence and is not supplied, use timeless explanations or explicitly express uncertainty. No manufactured promises of viral reach.
VOICE READABILITY for Russian: spell ё wherever it is pronounced; expand numbers, units, acronyms and abbreviations to natural spoken Russian in narration (for example, 'пять километров', not '5 км'). Avoid difficult foreign words, unnatural declensions, overlong clauses, and stage directions. A compelling hook must sound like a real Russian creator, not translated marketing copy. Captions can keep digits. Do not add pronunciation markup inside the JSON.
Each image_prompt: concrete portrait 9:16 illustration brief in ENGLISH, describe visual objects, no lettering, text, branding or watermarks. Every scene should visually differ. Caption: 3–8 impactful words; narration: intelligible and speakable. Include a short truthful description for the upload.
Return ONLY valid JSON with EXACT shape:
{{"title":"...","description":"...","scenes":[{{"narration":"...","caption":"...","image_prompt":"..."}}]}}
"""


def demo_story(topic: str) -> Story:
    return Story(
        title=f"{topic}: коротко и наглядно",
        description="Демонстрация локального видеоконвейера без платных API. Текст в этом примере не является фактическим объяснением.",
        scenes=[
            Scene(f"Сегодня попробуем разобраться: {topic}.", "ВОТ КАКОЙ ВОПРОС", "Bold question mark in front of abstract geometric shapes"),
            Scene("Для начала зададим правильный вопрос и выделим самое важное.", "НАЧНЁМ С ГЛАВНОГО", "Magnifying glass over a colorful schematic"),
            Scene("Затем посмотрим на простой пример и сравним возможные варианты.", "РАЗБЕРЁМ НА ПРИМЕРЕ", "Two illustrated cards with simple shapes"),
            Scene("Это демонстрационный ролик. Подключите API для настоящего сценария и картинок.", "ВКЛЮЧИ ГЕНЕРАЦИЮ", "Sparkling studio lights around a video frame"),
        ],
    )
