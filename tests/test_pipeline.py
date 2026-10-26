"""Production guardrails: mocked billable stages, no credentials or network."""
import json

from shorts_factory.cli import main
from shorts_factory.story import demo_story


def test_zero_cap_blocks_script_before_request(monkeypatch, tmp_path):
    import shorts_factory.cli as cli
    monkeypatch.setattr(cli, "create_story", lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid call")))
    assert main(["--topic", "Тема", "--out", str(tmp_path)]) == 1
    ledger = json.loads(next(tmp_path.glob("*/cost_ledger.json")).read_text())
    assert ledger["events"] == []


def test_total_budget_applies_across_variants(monkeypatch, tmp_path):
    import shorts_factory.cli as cli
    import shorts_factory.motion as motion
    import shorts_factory.video_quality_gate as gate
    calls = []
    def make_story(*args):
        calls.append(1)
        return demo_story("Тест")
    def render(story, audio, dest, **kwargs):
        path = dest / "short.mp4"
        path.write_bytes(b"mock")
        return path, [3] * 4
    monkeypatch.setattr(cli, "create_story", make_story)
    monkeypatch.setattr(motion, "render_motion", render)
    monkeypatch.setattr(gate, "inspect_video", lambda *a, **k: {"automated_pass": True})
    assert main(["--topic", "Тема", "--image-provider", "procedural", "--voice-provider", "none",
                 "--variants", "3", "--max-cost-usd", "0.10", "--out", str(tmp_path)]) == 1
    assert len(calls) == 1
    assert json.loads(next(tmp_path.glob("*/cost_ledger.json")).read_text())["reserved_estimate_usd"] == .08


def test_dry_run_does_not_write_or_call(monkeypatch, tmp_path, capsys):
    import shorts_factory.cli as cli
    monkeypatch.setattr(cli, "create_story", lambda *a: (_ for _ in ()).throw(AssertionError("paid call")))
    assert main(["--pilot", "--dry-run", "--out", str(tmp_path)]) == 0
    assert not list(tmp_path.iterdir())
    assert "Approximate" in capsys.readouterr().out


def test_azure_preview_zero_budget_prevents_real_call(monkeypatch, tmp_path):
    import shorts_factory.azure_speech as azure
    monkeypatch.setattr(azure, "synthesize_azure", lambda *a, **k: (_ for _ in ()).throw(AssertionError("API called")))
    assert main(["--voice-provider", "azure", "--voice-preview", "Проверка голоса.", "--out", str(tmp_path)]) == 1


def test_neural_preview_uses_wav_without_other_providers(monkeypatch, tmp_path):
    import shorts_factory.local_tts as neural
    calls = []
    def fake(text, path, **kwargs):
        calls.append(kwargs)
        path.write_bytes(b"mock wav")
        return path
    monkeypatch.setattr(neural, "synthesize_local", fake)
    assert main(["--voice-provider", "local", "--voice-preview", "Проверка голоса.", "--out", str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0]["speed"] == 1
    assert next(tmp_path.glob("voice-preview-*/audio_00.wav")).is_file()
