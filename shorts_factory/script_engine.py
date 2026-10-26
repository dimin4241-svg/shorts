"""Reviewed Russian pilot scripts and their editorial evidence.

No network or paid model calls occur here. Content ships as package resources so
installed CLI users receive the same reviewed scripts as a source checkout.
Timing hints are editorial targets, never forced speech alignment.
"""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json

from .story import Scene, Story

_SUPPORTED_SECONDS = (20, 28, 40)


def _read(name: str) -> dict:
    return json.loads(files("shorts_factory").joinpath("content", name).read_text(encoding="utf-8"))


def catalog() -> list[dict]:
    """Return independent copies of 15 ranked ideas, including source gaps."""
    return deepcopy(_read("ideas.json")["ideas"])


def facts_for(topic_id: str) -> list[dict]:
    """Return fact cards with verified URLs and explicit limits on each claim."""
    if topic_id not in {row["id"] for row in catalog()}:
        raise ValueError(f"Unknown topic_id: {topic_id}")
    data = _read("facts.json")
    cards = []
    for card in data["claims"]:
        if topic_id in card["topic_ids"]:
            item = deepcopy(card)
            item["sources"] = [deepcopy(data["sources"][key]) for key in card["source_ids"]]
            cards.append(item)
    return cards


def plan_for(topic_id: str = "submarine-cables", seconds: int = 28) -> dict:
    """Return a full production plan, including motion kinds and chosen hook."""
    if type(seconds) is not int or seconds not in _SUPPORTED_SECONDS:
        raise ValueError("Reviewed durations are 20, 28 and 40 seconds")
    plans = _read("pilots.json")["pilots"]
    if topic_id not in plans:
        raise ValueError(f"No reviewed pilot for {topic_id!r}; use catalog() to select a ready topic")
    plan = deepcopy(plans[topic_id])
    variant = plan.pop("variants")[str(seconds)]
    plan.update(variant)
    plan["topic_id"] = topic_id
    plan["target_seconds"] = seconds
    plan["chosen_hook"] = plan["hooks"][plan["chosen_hook_index"]]["text"]
    return plan


def load_pilot(topic_id: str = "submarine-cables", seconds: int = 28) -> Story:
    """Load a reviewed factual story without changing the existing Story schema."""
    plan = plan_for(topic_id, seconds)
    return Story(
        title=plan["title"],
        description=plan["description"],
        scenes=[Scene(row["narration"], row["caption"], row["image_prompt"]) for row in plan["scenes"]],
    )


def alternatives(topic_id: str = "submarine-cables") -> dict:
    """Return the three scored hooks and all reviewed duration alternatives."""
    plan = plan_for(topic_id)
    return {
        "topic_id": topic_id,
        "chosen_hook_index": plan["chosen_hook_index"],
        "chosen_hook": plan["chosen_hook"],
        "hooks": deepcopy(plan["hooks"]),
        "durations": {str(seconds): load_pilot(topic_id, seconds).as_dict() for seconds in _SUPPORTED_SECONDS},
    }
