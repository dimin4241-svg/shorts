import json
import shutil
import subprocess

import pytest

from shorts_factory.audio_edit import prepare_narration


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg not installed")
def test_only_long_trailing_padding_is_trimmed(tmp_path):
    source = tmp_path / "speech.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000:d=0.35", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.55", "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000:d=0.9", "-f", "lavfi", "-i",
        "sine=frequency=660:duration=0.55", "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000:d=0.9", "-filter_complex",
        "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[out]", "-map", "[out]", "-c:a", "pcm_s16le", str(source),
    ], check=True)
    prepared, metadata = prepare_narration(source, tmp_path / "run", 0)
    assert metadata["time_stretch"] is False
    assert metadata["speech_content_changed"] is False
    assert metadata["removed_seconds"] > 0.5
    assert metadata["prepared_duration_seconds"] < metadata["source_duration_seconds"] - 0.5
    pauses = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(prepared),
        "-af", "silencedetect=n=-40dB:d=0.65", "-f", "null", "-"], capture_output=True, text=True)
    assert "silence_duration: 0.9" in pauses.stderr
    leading = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(prepared),
        "-af", "silencedetect=n=-40dB:d=0.25", "-f", "null", "-"], capture_output=True, text=True)
    assert "silence_start: 0" in leading.stderr
    assert json.loads(prepared.with_suffix(".json").read_text())["source_sha256"] == metadata["source_sha256"]
