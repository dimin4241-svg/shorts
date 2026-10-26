import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import ImageChops, ImageStat

from shorts_factory.caption_engine import build_caption_cues
from shorts_factory.motion import CABLE_KINDS, PHONE_KINDS, MotionScene, choose_motion_kinds, render_motion, scene_durations
from shorts_factory.story import Scene, Story


def test_scene_duration_uses_audio_not_requested_target(monkeypatch):
    monkeypatch.setattr('shorts_factory.motion.media_duration', lambda _: 9.23)
    durations = scene_durations([Path('voice.wav'), None], target_seconds=4, fps=30)
    assert durations[0] >= 9.23 + .08
    assert durations[0] < 9.23 + .08 + 1/30
    assert durations[1] == 2.


def test_last_spoken_scene_has_a_brief_natural_ending_hold(monkeypatch):
    monkeypatch.setattr('shorts_factory.motion.media_duration', lambda audio: {'line.wav': 2.0, 'cta.wav': 2.4}[Path(audio).name])
    durations = scene_durations([Path('line.wav'), Path('cta.wav')], target_seconds=4, fps=30)
    assert durations[0] >= 2.08
    assert durations[0] < 2.08 + 1/30
    assert durations[1] >= 3.23
    assert durations[1] < 3.23 + 1/30


@pytest.mark.parametrize('fps', [24, 30])
def test_supported_frame_rates_do_not_drop_audio(monkeypatch, fps):
    monkeypatch.setattr('shorts_factory.motion.media_duration', lambda _: 4.07)
    result = scene_durations([Path('voice.wav')], target_seconds=1, fps=fps)[0]
    assert result >= 4.15
    assert abs(round(result*fps) - result*fps) < .00001


@pytest.mark.parametrize('kind', CABLE_KINDS + ['fiber_detail', 'airflow', 'power', 'orbit', 'tire', 'tire_tread', 'sound'] + PHONE_KINDS + ['battery_flow', 'voltage_drop'])
def test_every_composition_has_real_object_motion_and_safe_text(kind):
    s = Story('Кабели', 'Тест', [Scene('Данные проходят под водой.', 'Интернет под водой', '')])
    cues = build_caption_cues(s, [4.])
    scene = MotionScene(s, 0, 4., 180, 320, 'explainer', kind, cues)
    first, second = scene.frame(.8), scene.frame(2.3)
    # Inspect only the diagram area: changing phrase captions cannot fake motion.
    crop = (18, 90, 160, 208)
    assert ImageChops.difference(first.crop(crop), second.crop(crop)).getbbox()
    assert all(row['inside_safe_zone'] for row in scene.text_geometries)


def test_unknown_animation_rejected_before_render(tmp_path):
    s = Story('Тест', 'Тест', [Scene('Текст.', 'Тест', '')])
    with pytest.raises(ValueError, match='Unknown motion'):
        render_motion(s, [None], tmp_path, motion_kinds=['unknown'])


def test_cable_story_has_all_six_compositions():
    s = Story('Подводные кабели', 'Тест', [Scene('Связь.', 'Связь', '')]*6)
    assert choose_motion_kinds(s) == CABLE_KINDS


def test_cold_phone_story_gets_a_phone_specific_visual_sequence():
    story = Story('Почему телефон гаснет на морозе?', 'Тест',
                  [Scene('Телефон отключился.', 'Телефон выключен', '') for _ in range(5)])
    assert choose_motion_kinds(story) == PHONE_KINDS


def test_phone_schematic_label_stays_between_heading_and_illustration():
    story = Story('Телефон', 'Тест', [Scene('Экран погас.', 'ЗАРЯД ИСЧЕЗ?', '')])
    scene = MotionScene(story, 0, 4., 360, 640, 'explainer', 'phone_cold',
                        build_caption_cues(story, [4.]))
    title = next(row for row in scene.text_geometries if row['role'] == 'scene_heading')
    label = next(row for row in scene.text_geometries if row['role'] == 'diagram_disclosure')
    assert title['bounds'][3] < label['bounds'][1]
    assert label['bounds'][3] < 640 * .285


def test_final_cta_title_fits_and_last_frame_fades_to_designed_background():
    scenes = [Scene('А если кабель повреждён?', 'ЗНАЛ ОБ ЭТОМ? ПИШИ В КОММЕНТАРИЯХ И СТАВЬ ЛАЙК', '')]
    story = Story('Подводные кабели', 'Тест', scenes)
    cues = build_caption_cues(story, [4.])
    scene = MotionScene(story, 0, 4., 360, 640, 'explainer', 'globe_reveal', cues)
    assert all(row['inside_safe_zone'] for row in scene.text_geometries)
    middle = scene.frame(3.3)
    ending = scene.frame(3.99)
    middle_delta = sum(ImageStat.Stat(ImageChops.difference(scene.base, middle)).mean)
    ending_delta = sum(ImageStat.Stat(ImageChops.difference(scene.base, ending)).mean)
    assert ending_delta < middle_delta * .1
    assert ImageChops.difference(scene.base, scene.frame(4.)).getbbox() is None


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg not installed')
def test_local_motion_mp4_streams_and_manifests(tmp_path):
    s = Story('Кабели', 'Тест', [Scene('Данные идут по дну океана.', 'Интернет под водой', '')]*3)
    output, durations = render_motion(s, [None]*3, tmp_path, width=180, height=320, fps=12, target_seconds=3)
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)]))
    streams = info['streams']
    assert {row['codec_name'] for row in streams} == {'h264', 'aac'}
    video = next(row for row in streams if row['codec_type'] == 'video')
    assert (video['width'], video['height']) == (180, 320)
    assert int(video['nb_frames']) == round(sum(durations)*12)
    geometry = json.loads((tmp_path / 'geometry.json').read_text())
    assert geometry['all_text_within_safe_zone']
    timeline = json.loads((tmp_path / 'timeline.json').read_text())
    assert timeline['duration_policy'] == 'audio_first_no_speedup'
    assert all(row['visual_type'] == 'original_procedural_animation' for row in timeline['scenes'])
    assert len(list((tmp_path / 'previews').glob('scene_*.png'))) == 3
    assert (tmp_path / 'captions.ass').is_file()


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg not installed')
def test_master_two_pass_loudness_uses_real_measurement_and_preserves_duration(tmp_path):
    # A deterministic tone exercises audio plumbing without invoking speech APIs.
    tone = tmp_path / 'test-tone.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=2', '-c:a', 'pcm_s16le', str(tone)], check=True)
    s = Story('Технический тест', 'Технический тест', [Scene('Проверка звука.', 'Проверка звука', '')])
    output, durations = render_motion(s, [tone], tmp_path, width=180, height=320, fps=24,
                                     target_seconds=1, motion_kinds=['fiber_detail'], style='whiteboard')
    assert durations[0] >= 2.08
    master = json.loads((tmp_path / 'audio_master.json').read_text())
    assert master['method'] == 'two_pass_ebu_r128'
    assert master['analysis']['input_i']
    assert master['no_time_stretch'] is True
    timeline = json.loads((tmp_path / 'timeline.json').read_text())
    assert timeline['style'] == 'explainer' and timeline['requested_style'] == 'whiteboard'
    analysis = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(output), '-vn',
                               '-af', 'loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json', '-f', 'null', '-'],
                              capture_output=True, text=True)
    measurement = json.JSONDecoder().raw_decode(analysis.stderr[analysis.stderr.rfind('{'):])[0]
    assert -16.4 <= float(measurement['input_i']) <= -15.6
    assert float(measurement['input_tp']) <= -1.0
