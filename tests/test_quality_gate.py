import json
from pathlib import Path
from subprocess import CompletedProcess

from shorts_factory.video_quality_gate import inspect_video


def test_missing_file_is_failure_with_repair_stages(tmp_path):
    report = inspect_video(tmp_path / "missing.mp4")
    assert not report["automated_pass"]
    assert report["publication_approved"] is False
    assert "render" in report["repair_stages"]
    assert (tmp_path / "quality_report.json").is_file()


def test_black_silence_clipping_are_critical(monkeypatch, tmp_path):
    import shorts_factory.video_quality_gate as gate
    video = tmp_path / "short.mp4"
    video.write_bytes(b"stub")
    def fake_run(cmd):
        if cmd[0] == "ffprobe":
            return CompletedProcess(cmd, 0, json.dumps({"streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "28"}}), "")
        return CompletedProcess(cmd, 0, "", 'black_duration:0.50\nsilence_duration: 3.0\n{"input_i":"-16.0","input_tp":"0.1"}')
    monkeypatch.setattr(gate, "_run", fake_run)
    report = inspect_video(video)
    statuses = {c["name"]: c["status"] for c in report["checks"]}
    assert statuses["black_frames"] == statuses["excessive_silence"] == statuses["clipping"] == "fail"
    assert statuses["speech_intelligibility"] == "review"
    assert statuses["loudness"] == "pass"
