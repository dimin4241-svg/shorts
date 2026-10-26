"""Offline contracts for the optional neural voice; no MLX import or model fetch."""
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
import wave

import pytest

from shorts_factory import local_tts


def write_wav(path, *, rate=8000, values=None):
    values = values if values is not None else [0.1] * 2400
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, value)) * 32767)) for value in values))


def test_default_config_is_original_russian_voice_and_never_imports_mlx(monkeypatch):
    monkeypatch.delenv("SHORTS_LOCAL_TTS_MODEL", raising=False)
    config = local_tts.local_tts_config()
    assert config["model"] == local_tts.DEFAULT_MODEL
    assert config["language"] == "Russian"
    assert "male native Russian" in config["instruct"]
    assert "No imitation" in config["instruct"]
    assert config["max_tokens"] == 1024 and config["temperature"] == 0.6
    assert config["download_policy"] == "local_cache_only"
    assert "mlx_audio" not in sys.modules
    assert local_tts.local_tts_config(delivery="natural")["delivery"] == "natural"


def test_runner_subprocess_contract_offline_settings_metadata_and_no_shell(monkeypatch, tmp_path):
    monkeypatch.setenv("SHORTS_LOCAL_TTS_PYTHON", sys.executable)
    monkeypatch.setenv("SHORTS_LOCAL_TTS_MODEL", "/models/prepared-qwen")
    monkeypatch.setenv("PRIVATE_EXAMPLE_API_KEY", "never-record-this")
    called = {}

    def run(command, **kwargs):
        called.update(command=command, **kwargs)
        payload = json.loads(kwargs["input"])
        assert payload["text"] == "Наш интернет проходит под водой."
        assert payload["config"]["language"] == "Russian"
        assert "No imitation" in payload["config"]["instruct"]
        write_wav(payload["output"])
        metadata = {**payload["config"], "resolved_model": "/models/prepared-qwen", "time_stretched": False}
        return subprocess.CompletedProcess(command, 0, stdout="Model loading message\n" + local_tts.RESULT_PREFIX + json.dumps({"status": "ok", "metadata": metadata}) + "\n", stderr="")

    monkeypatch.setattr(local_tts.subprocess, "run", run)
    destination = tmp_path / "a.wav"
    assert local_tts.synthesize_local("Наш интернет проходит под водой.", destination) == destination
    assert called["command"][0] == sys.executable
    assert called["command"][1] == str(Path(local_tts.__file__).resolve())
    assert called["command"][2:] == ["--runner"]
    assert "shell" not in called
    assert called["env"]["HF_HUB_OFFLINE"] == "1"
    assert called["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert called["timeout"] == 900
    sidecar = destination.with_suffix(".tts.json").read_text()
    metadata = json.loads(sidecar)
    assert metadata["samples"] == 2400 and metadata["duration_seconds"] == 0.3
    assert metadata["resolved_model"] == "/models/prepared-qwen"
    assert "never-record-this" not in sidecar


@pytest.mark.parametrize("speed", [1.04, 0.9, 2, float("nan"), True])
def test_reject_speed_changes_before_starting_optional_runtime(monkeypatch, tmp_path, speed):
    monkeypatch.setattr(local_tts.subprocess, "run", lambda *a, **k: pytest.fail("Unexpected subprocess"))
    with pytest.raises(ValueError, match="speed=1.0"):
        local_tts.synthesize_local("Проверка.", tmp_path / "a.wav", speed=speed)


def test_missing_optional_interpreter_has_no_implicit_install_or_download(monkeypatch, tmp_path):
    monkeypatch.delenv("SHORTS_LOCAL_TTS_PYTHON", raising=False)
    monkeypatch.setattr(local_tts.subprocess, "run", lambda *a, **k: pytest.fail("Unexpected subprocess"))
    with pytest.raises(RuntimeError, match="SHORTS_LOCAL_TTS_PYTHON"):
        local_tts.synthesize_local("Проверка.", tmp_path / "a.wav")


@pytest.mark.parametrize("failure", ["exit", "no-output", "invalid-wav", "timeout"])
def test_failed_runner_preserves_prior_audio_and_hides_external_error_text(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("SHORTS_LOCAL_TTS_PYTHON", sys.executable)
    destination = tmp_path / "a.wav"
    destination.write_bytes(b"previous-audition")

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 900, stderr="secret-token")
        payload = json.loads(kwargs["input"])
        if failure == "invalid-wav":
            Path(payload["output"]).write_bytes(b"not wave")
        response = {"status": "error", "error": "model_not_cached"} if failure == "exit" else {"status": "ok", "metadata": {}}
        return subprocess.CompletedProcess(command, 1 if failure == "exit" else 0,
                                           stdout=local_tts.RESULT_PREFIX + json.dumps(response), stderr="secret-token")

    monkeypatch.setattr(local_tts.subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        local_tts.synthesize_local("Проверка.", destination)
    assert destination.read_bytes() == b"previous-audition"
    assert "secret-token" not in str(error.value)
    assert not destination.with_suffix(".tts.json").exists()


def test_resolve_model_cache_only_and_reuse_loaded_model(monkeypatch, tmp_path):
    cache_calls, load_calls = [], []
    def snapshot(**kwargs):
        cache_calls.append(kwargs)
        return str(tmp_path)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot))
    assert local_tts._resolve_model_path("mlx-community/prepared") == tmp_path
    assert cache_calls == [{"repo_id": "mlx-community/prepared", "local_files_only": True}]
    model = SimpleNamespace(generate_voice_design=lambda **kwargs: iter(()))
    def load(path):
        load_calls.append(path)
        return model
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", SimpleNamespace(load_model=load))
    local_tts._load_model.cache_clear()
    assert local_tts._load_model(str(tmp_path)) is model
    assert local_tts._load_model(str(tmp_path)) is model
    assert load_calls == [tmp_path]
    local_tts._load_model.cache_clear()


class FakeArray:
    def __init__(self, values):
        self.values = list(values)
        self.ndim = 1
        self.size = len(self.values)
    def squeeze(self):
        return self
    def __len__(self):
        return len(self.values)
    def __mul__(self, gain):
        return FakeArray(value * gain for value in self.values)


def numerical_stubs(monkeypatch):
    # Model and numerical dependencies are optional: these stubs exercise the
    # adapter's API/validation without installing a GPU package in the test venv.
    numpy = SimpleNamespace(
        float32="float32", asarray=lambda values, dtype: FakeArray(values),
        isfinite=lambda array: SimpleNamespace(all=lambda: all(math.isfinite(value) for value in array.values)),
        concatenate=lambda arrays: FakeArray(value for array in arrays for value in array.values),
        abs=lambda array: FakeArray(abs(value) for value in array.values),
        max=lambda array: max(array.values),
    )
    writes = []
    def sound_write(path, audio, rate, **kwargs):
        writes.append({"rate": rate, "audio": audio.values, **kwargs})
        write_wav(path, rate=rate, values=audio.values)
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=sound_write))
    return writes


def test_voice_design_exact_method_and_parameters_preserve_duration(monkeypatch, tmp_path):
    writes = numerical_stubs(monkeypatch)
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        yield SimpleNamespace(audio=[1.2] * 2400, sample_rate=8000, token_count=30)
    monkeypatch.setattr(local_tts, "_resolve_model_path", lambda name: tmp_path)
    monkeypatch.setattr(local_tts, "_load_model", lambda name: SimpleNamespace(generate_voice_design=generate))
    config = local_tts.local_tts_config(delivery="energetic")
    output = tmp_path / "generated.wav"
    metadata = local_tts._generate_wav({"text": "Ёж идёт домой.", "output": str(output), "config": config})
    assert calls == [{"text": "Ёж идёт домой.", "language": "Russian", "instruct": config["instruct"],
                      "max_tokens": 1024, "temperature": 0.6, "stream": False, "verbose": False}]
    assert "speed" not in calls[0]
    assert len(writes[0]["audio"]) == 2400
    assert math.isclose(max(writes[0]["audio"]), 0.98, abs_tol=1e-8)
    assert writes[0]["subtype"] == "PCM_16"
    assert metadata["time_stretched"] is False
    assert metadata["amplitude_gain"] < 1
    assert metadata["duration_seconds"] == 0.3


@pytest.mark.parametrize("values,tokens", [([], 10), ([float("nan")] * 2400, 10), ([0] * 2400, 10), ([0.1] * 2400, 1024)])
def test_invalid_synthesis_audio_or_generation_limit_never_writes_wav(monkeypatch, tmp_path, values, tokens):
    writes = numerical_stubs(monkeypatch)
    monkeypatch.setattr(local_tts, "_resolve_model_path", lambda name: tmp_path)
    monkeypatch.setattr(local_tts, "_load_model", lambda name: SimpleNamespace(generate_voice_design=lambda **kwargs:
                        iter([SimpleNamespace(audio=values, sample_rate=8000, token_count=tokens)])))
    with pytest.raises(ValueError):
        local_tts._generate_wav({"text": "Проба.", "output": str(tmp_path / "bad.wav"), "config": local_tts.local_tts_config()})
    assert writes == []
