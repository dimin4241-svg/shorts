import json

import pytest

from shorts_factory.budget import Budget, cached_artifact


def test_budget_reserves_before_request_and_keeps_unknown_failure(tmp_path):
    ledger = tmp_path / "ledger.json"
    budget = Budget(ledger, .2, {"voice": .1})
    def failed():
        assert json.loads(ledger.read_text())["events"][0]["status"] == "attempted"
        raise RuntimeError("network timeout")
    with pytest.raises(RuntimeError, match="timeout"):
        budget.call("voice", "yandex", .08, failed)
    with pytest.raises(RuntimeError, match="Budget blocked"):
        budget.call("voice", "yandex", .03, lambda: pytest.fail("must not contact API"))
    assert Budget(ledger, .2).reserved == .08


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), -1])
def test_invalid_budget_cannot_bypass_comparison(tmp_path, cap):
    with pytest.raises(ValueError):
        Budget(tmp_path / "ledger.json", cap)


def test_zero_budget_blocks_paid_call(tmp_path):
    budget = Budget(tmp_path / "ledger.json")
    with pytest.raises(RuntimeError, match="Budget blocked"):
        budget.call("images", "openai", .12, lambda: pytest.fail("must not call"))
    assert budget.events == []


def test_cache_requires_settings_and_actual_content(tmp_path):
    asset = tmp_path / "audio.mp3"
    calls = []
    def generate():
        calls.append(1)
        asset.write_bytes(b"original")
        return asset
    cached_artifact(asset, {"text": "one"}, generate)
    cached_artifact(asset, {"text": "one"}, generate)
    assert len(calls) == 1
    asset.write_bytes(b"damaged")
    cached_artifact(asset, {"text": "one"}, generate)
    cached_artifact(asset, {"text": "two"}, generate)
    assert len(calls) == 3
