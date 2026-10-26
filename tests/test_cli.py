from shorts_factory.cli import main


def test_plan_only_never_calls_api(capsys):
    assert main(["--topic", "Как летает самолёт", "--plan-only"]) == 0
    assert "possible_billable_calls" in capsys.readouterr().out


def test_bad_ratio():
    assert main(["--topic", "test", "--width", "800", "--height", "800", "--demo"]) == 1


def test_demo_local_mp4(tmp_path):
    from shutil import which
    if not which("ffmpeg") or not which("ffprobe"):
        return
    result = main(["--topic", "Эксперимент", "--demo", "--seconds", "12", "--width", "360", "--height", "640", "--fps", "12", "--out", str(tmp_path)])
    assert result == 0
    assert len(list(tmp_path.glob("*/v1/short.mp4"))) == 1
    assert len(list(tmp_path.glob("*/v1/subtitles.srt"))) == 1
    assert len(list(tmp_path.glob("*/v1/metadata.json"))) == 1
