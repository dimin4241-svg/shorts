"""Conservative request reservations, never a claim about a provider invoice.

Failed/unknown requests keep their reservation: a timeout can still be billed.
There are deliberately no automatic retries of billable requests.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")
STAGES = ("research", "scripts", "images", "voice")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


class Budget:
    def __init__(self, path: Path, maximum: float = 0, limits: dict | None = None):
        self.path = path
        self.maximum = self._number(maximum)
        self.limits = {name: self._number((limits or {}).get(name, maximum)) for name in STAGES}
        self.events = json.loads(path.read_text(encoding="utf-8"))["events"] if path.exists() else []
        self.save()

    @staticmethod
    def _number(value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("Budget values must be finite and non-negative")
        return float(value)

    @property
    def reserved(self) -> float:
        return sum(event["reserved_estimate_usd"] for event in self.events)

    def save(self) -> None:
        save_json(self.path, {"cost_basis": "approximate planning reservations, not provider invoices",
                             "max_cost_usd": self.maximum, "stage_limits_usd": self.limits,
                             "reserved_estimate_usd": round(self.reserved, 6), "events": self.events})

    def call(self, stage: str, provider: str, estimate: float, action: Callable[[], T]) -> T:
        if stage not in STAGES:
            raise ValueError(f"Unknown budget stage: {stage}")
        estimate = self._number(estimate)
        if provider in {"openai", "anthropic", "claude-svg", "yandex", "elevenlabs", "azure"} and estimate <= 0:
            raise ValueError("Paid requests require a positive estimate")
        stage_used = sum(e["reserved_estimate_usd"] for e in self.events if e["stage"] == stage)
        if self.reserved + estimate > self.maximum + 1e-9 or stage_used + estimate > self.limits[stage] + 1e-9:
            raise RuntimeError(f"Budget blocked {stage}/{provider}: set an explicit --max-cost-usd and stage limit. Estimates are approximate.")
        event = {"stage": stage, "provider": provider, "reserved_estimate_usd": estimate, "status": "attempted"}
        self.events.append(event)
        self.save()  # reserve before contacting a provider
        try:
            result = action()
        except Exception:
            event["status"] = "failed_or_unknown"
            self.save()
            raise
        event["status"] = "completed"
        self.save()
        return result


def cached_artifact(path: Path, settings: object, action: Callable[[], Path | None]) -> Path | None:
    """Reuse only matching settings AND matching file content, never stale paid media."""
    record = path.with_suffix(path.suffix + ".cache.json")
    key = fingerprint(settings)
    if path.exists() and record.exists():
        try:
            previous = json.loads(record.read_text(encoding="utf-8"))
            if previous == {"key": key, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}:
                return path
        except (ValueError, OSError):
            pass
    output = action()
    if output is not None:
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError("Provider returned no artifact")
        save_json(record, {"key": key, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
    return output
