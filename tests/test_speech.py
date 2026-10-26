"""Offline provider-contract tests: no API keys, billing or internet required."""
import json
from pathlib import Path

import pytest

from shorts_factory import speech
from shorts_factory.cli import main
from shorts_factory.voice import make_audio


def test_safe_russian_glossary(tmp_path):
    config = tmp_path / "words.json"
    config.write_text(json.dumps({
        "replacements": {"ИИ": "искусственный интеллект", "все-таки": "всё-таки"},
        "yandex_stress": {"замок": "зам+ок"},
    }, ensure_ascii=False), encoding="utf-8")
    glossary = speech.load_glossary(str(config))
    assert speech.prepare_speech_text("ИИ. Новый замок, но не замочек.", "yandex", glossary) == (
        "искусственный интеллект. Новый зам+ок, но не замочек.")
    assert speech.prepare_speech_text("Новый замок", "elevenlabs", glossary) == "Новый замок"


@pytest.mark.parametrize("entry", [
    {"yandex_stress": {"замок": "за+мок"}},
    {"yandex_stress": {"замок": "<script>"}},
    {"replacements": {"нет": "\ncommand"}},
    {"bad_section": {"a": "b"}},
])
def test_reject_invalid_pronunciation(tmp_path, entry):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        speech.load_glossary(str(path))


def test_require_elevenlabs_voice(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_VOICE_ID"):
        speech.provider_voice("elevenlabs", "")


def test_yandex_payload(monkeypatch, tmp_path):
    recorded = {}
    monkeypatch.setenv("YANDEX_API_KEY", "hidden-test-key")
    def fake_post(url, *, headers, payload, form=False):
        recorded.update(url=url, headers=headers, payload=payload, form=form)
        return b"fake mp3"
    monkeypatch.setattr(speech, "_post", fake_post)
    output = tmp_path / "a.mp3"
    speech.synthesize_audio("yandex", "Он открыл зам+ок.", output, voice="", language="ru", speed=1.04, delivery="energetic")
    assert output.read_bytes() == b"fake mp3"
    assert recorded["url"] == "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
    assert recorded["payload"]["voice"] == "ermil"
    assert recorded["payload"]["format"] == "mp3"
    assert recorded["payload"]["text"].endswith("зам+ок.")
    assert recorded["form"] is True


def test_elevenlabs_continuity(monkeypatch, tmp_path):
    recorded = {}
    monkeypatch.setenv("ELEVENLABS_API_KEY", "hidden-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "ruVoice98765")
    monkeypatch.setattr(speech, "_post", lambda url, **kwargs: (recorded.update(url=url, **kwargs) or b"mp3"))
    output = tmp_path / "b.mp3"
    speech.synthesize_audio("elevenlabs", "Сейчас расскажу.", output, voice="",
                            language="ru", speed=1.04, delivery="storytelling",
                            previous_text="Это начало.", next_text="Вот почему.")
    assert recorded["payload"]["previous_text"] == "Это начало."
    assert recorded["payload"]["next_text"] == "Вот почему."
    assert recorded["payload"]["model_id"] == "eleven_multilingual_v2"
    assert recorded["payload"]["voice_settings"]["speed"] == 1.04
    assert recorded["url"].endswith("/ruVoice98765")
    assert output.read_bytes() == b"mp3"


def test_openai_russian_style(monkeypatch, tmp_path):
    recorded = {}
    monkeypatch.setenv("OPENAI_API_KEY", "hidden-key")
    monkeypatch.setattr(speech, "_post", lambda url, **kwargs: (recorded.update(url=url, **kwargs) or b"mp3"))
    speech.synthesize_audio("openai", "Это важно.", tmp_path / "x.mp3",
                            voice="", language="ru", speed=1.05, delivery="energetic")
    assert recorded["payload"]["voice"] == "cedar"
    assert "русском" in recorded["payload"]["instructions"]
    assert recorded["payload"]["speed"] == 1.05


def test_preview_makes_only_audio(monkeypatch, tmp_path):
    import shorts_factory.cli as cli
    called = []
    def mocked_voice(text, out, idx, **kwargs):
        called.append((text, kwargs))
        file = out / "audio_00.mp3"
        file.write_bytes(b"test")
        return file
    monkeypatch.setattr(cli, "make_audio", mocked_voice)
    assert main(["--voice-preview", "Это проверка русского голоса!", "--voice-provider", "yandex",
                 "--max-cost-usd", "0.1", "--out", str(tmp_path)]) == 0
    assert len(called) == 1
    assert len(list(tmp_path.glob("voice-preview-*/audio_00.mp3"))) == 1
    assert not list(tmp_path.glob("**/image*"))


def test_demo_stays_offline_with_new_default(monkeypatch, tmp_path):
    import shorts_factory.voice as voice_mod
    monkeypatch.setattr(voice_mod, "synthesize_audio", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Paid API called")))
    from shutil import which
    if not which("ffmpeg") or not which("ffprobe"):
        pytest.skip("FFmpeg not installed")
    assert main(["--topic", "Проба", "--demo", "--seconds", "12", "--width", "360",
                 "--height", "640", "--fps", "12", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*/v1/short.mp4"))) == 1


def test_macos_native_method_and_measured_speed(monkeypatch, tmp_path):
    import shorts_factory.voice as voice_module
    from subprocess import CompletedProcess
    monkeypatch.setattr(voice_module.platform, "system", lambda: "Darwin")
    calls = []
    def say(command, **kwargs):
        calls.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(b"local-audio")
        return CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(voice_module.subprocess, "run", say)
    audio = make_audio("Он идёт по дну.", tmp_path, 0, provider="macos", voice="Milena", language="ru", speed=1.0)
    assert audio.suffix == ".aiff"
    assert calls[0][:5] == ["say", "-v", "Milena", "-r", "160"]
    assert calls[0][-1] == "Он идёт по дну."
