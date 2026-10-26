import json

import pytest
from PIL import Image, ImageDraw

from shorts_factory.caption_engine import build_caption_cues, display_text, text_layout, write_captions
from shorts_factory.story import Scene, Story


def story(text='Световые сигналы проходят по тонкому оптоволокну.'):
    return Story('Подводная связь', 'Тест', [Scene(text, 'Интернет под водой', '')])


def test_estimated_phrase_timing_is_honest_and_complete(tmp_path):
    s = story('На дне океана идут сообщения, видео и звонки.')
    manifest = write_captions(s, [4.7], tmp_path, width=360, height=640)
    cues = manifest['cues']
    assert manifest['timing_method'] == 'estimated_phrase'
    assert manifest['alignment_ran'] is False
    assert manifest['karaoke'] is False
    assert all(not cue['words'] for cue in cues)
    assert ' '.join(cue['text'] for cue in cues) == s.scenes[0].narration
    assert cues[0]['start'] == 0 and cues[-1]['end'] == 4.7
    assert all(a['end'] == b['start'] for a, b in zip(cues, cues[1:]))
    assert all(len(cue['text'].split()) <= 4 for cue in cues)
    assert r'\kf' not in (tmp_path / 'captions.ass').read_text()
    assert (tmp_path / 'captions.srt').read_text() == (tmp_path / 'subtitles.srt').read_text()
    assert json.loads((tmp_path / 'caption_manifest.json').read_text()) == manifest


def aligned():
    return {'source': 'forced_alignment', 'scenes': [{'words': [
        {'word': 'Световые', 'start': .1, 'end': .5},
        {'word': 'сигналы', 'start': .55, 'end': .9},
        {'word': 'проходят', 'start': .95, 'end': 1.3},
        {'word': 'по', 'start': 1.35, 'end': 1.5},
        {'word': 'тонкому', 'start': 1.6, 'end': 1.9},
        {'word': 'оптоволокну.', 'start': 2., 'end': 2.6},
    ]}]}


def test_imported_timings_karaoke_only_after_token_and_time_validation(tmp_path):
    manifest = write_captions(story(), [3.0], tmp_path, word_timings=aligned())
    assert manifest['timing_mode'] == 'imported_word_timings'
    assert manifest['imported_source'] == 'forced_alignment'
    assert manifest['alignment_ran'] is False
    assert manifest['karaoke'] is True
    assert manifest['cues'][0]['start'] == .1
    assert manifest['cues'][-1]['end'] == 2.6
    assert r'\kf40' in (tmp_path / 'captions.ass').read_text()


@pytest.mark.parametrize('mutation', ['overlap', 'transcript', 'nonfinite', 'estimate', 'outside', 'missing'])
def test_reject_invalid_alignment(mutation):
    imported = aligned()
    words = imported['scenes'][0]['words']
    if mutation == 'overlap':
        words[1]['start'] = .4
    elif mutation == 'transcript':
        words[0]['word'] = 'Другие'
    elif mutation == 'nonfinite':
        words[0]['end'] = float('nan')
    elif mutation == 'estimate':
        imported['source'] = 'estimated'
    elif mutation == 'outside':
        words[-1]['end'] = 4
    elif mutation == 'missing':
        words.pop()
    with pytest.raises(ValueError):
        build_caption_cues(story(), [3.0], imported)


def test_stress_markup_is_never_visible():
    assert display_text('Оптоволокн+о, всё. К+абель 2 + 2. уд\u0301арение') == 'Оптоволокно, всё. Кабель 2 + 2. ударение'


@pytest.mark.parametrize('size', [(180, 320), (360, 640), (1080, 1920)])
def test_cyrillic_layout_small_brand_and_long_caption_fit(size):
    width, height = size
    draw = ImageDraw.Draw(Image.new('RGB', size))
    brand = text_layout(draw, 'НЕВИДИМЫЙ МИР', width=width, height=height, center_y=height*.12,
                        font_fraction=.027, max_lines=1)
    assert brand['inside_safe_zone']
    caption = text_layout(draw, 'Межконтинентальная связь', width=width, height=height, center_y=height*.75,
                          max_lines=2)
    assert caption['inside_safe_zone']
    assert ''.join(line['text'].replace(' ', '') for line in caption['lines']) == 'Межконтинентальнаясвязь'


def test_alignment_scene_offsets_are_absolute():
    s = Story('t', 'd', [Scene('Один.', 'Один', ''), Scene('Два.', 'Два', '')])
    data = {'source': 'manual', 'scenes': [{'words': [{'word': 'Один.', 'start': .1, 'end': .8}]},
                                         {'words': [{'word': 'Два.', 'start': .2, 'end': .9}]}]}
    cues = build_caption_cues(s, [1., 1.2], data)
    assert cues[1].start == 1.2 and cues[1].end == 1.9
    assert cues[1].words[0]['start'] == 1.2


def test_russian_prepositions_stay_with_following_word():
    s = story('Трафик могут направить по запасному пути. Беспроводной мир зависит от проводов под водой.')
    cues = build_caption_cues(s, [7.])
    assert ' '.join(cue.text for cue in cues) == s.scenes[0].narration
    assert not any(cue.text.split()[-1] in {'по', 'под', 'от'} for cue in cues)
