import json
import shutil
import subprocess

import pytest

from shorts_factory.audio_edit import prepare_narration


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg not installed")
def test_long_sentence_pauses_are_compressed_and_manifested(tmp_path):
    source = tmp_path / "speech.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.55", "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000:d=0.9", "-f", "lavfi", "-i",
        "sine=frequency=660:duration=0.55", "-filter_complex",
        "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]", "-map", "[out]", "-c:a", "pcm_s16le", str(source),
    ], check=True)
    prepared, metadata = prepare_narration(source, tmp_path / "run", 0)
    assert metadata["time_stretch"] is False
    assert metadata["speech_content_changed"] is False
    assert metadata["removed_seconds"] > 0.5
    assert metadata["prepared_duration_seconds"] < metadata["source_duration_seconds"] - 0.5
    assert json.loads(prepared.with_suffix(".json").read_text())["source_sha256"] == metadata["source_sha256"]
