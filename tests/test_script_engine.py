"""Protect reviewed content from losing evidence or silently mutating on export."""
from importlib.resources import files
import json

import pytest

from shorts_factory.script_engine import alternatives, catalog, facts_for, load_pilot, plan_for
from shorts_factory.story import Story, parse_story


def test_catalog_keeps_all_user_topics_original_ideas_and_transparent_scores():
    ideas = catalog()
    assert len(ideas) >= 15
    assert {row['user_candidate'] for row in ideas if row['user_candidate']} == set(range(1, 13))
    assert sum(row['original'] for row in ideas) >= 3
    weights = {'audience':25, 'originality':20, 'visual':20, 'retention':15, 'timeliness':10, 'verifiability':10}
    for row in ideas:
        assert len(set(row['hooks'])) == 3
        assert row['score'] == sum(row['score_components'].values())
        assert all(0 <= value <= weights[key] for key, value in row['score_components'].items())
        assert row['score_kind'] == 'editorial_hypothesis_not_view_prediction'
        assert row['competition'] is None
        assert row['source_urls']
        assert facts_for(row['id'])
    selected = [row['id'] for row in ideas if row['selected_top_five']]
    assert selected == [row['id'] for row in ideas[:5]]
    assert len(selected) == 5


def test_all_prepared_pilots_have_evidence_and_compatible_story_schema():
    for idea in catalog():
        if not idea['pilot_ready']:
            continue
        facts = {card['id']: card for card in facts_for(idea['id'])}
        for seconds in (20, 28, 40):
            plan = plan_for(idea['id'], seconds)
            story = load_pilot(idea['id'], seconds)
            assert isinstance(story, Story)
            assert parse_story(json.dumps(story.as_dict())) == story
            assert plan['chosen_hook'] == plan['hooks'][plan['chosen_hook_index']]['text']
            assert plan['scenes'][0]['narration'].startswith(plan['chosen_hook'])
            assert plan['scenes'][0]['start_hint'] == 0
            assert plan['scenes'][-1]['end_hint'] == seconds
            for row in plan['scenes']:
                assert row['motion_kind'] and row['planned_motion_kind'] and row['beats']
                assert row['fact_ids']
                for key in row['fact_ids']:
                    assert key in facts
                    assert facts[key]['sources']
                    assert all(src['url'].startswith('https://') and src['checked_at'] for src in facts[key]['sources'])


def test_first_pilot_is_short_spoken_russian_and_answers_why():
    story = load_pilot()
    narration = ' '.join(scene.narration for scene in story.scenes)
    # A real Dmitry audition needed a shorter script than a word-rate estimate.
    assert 45 <= len(narration.split()) <= 55
    assert 'разных длин волн' in narration
    assert 'небольшой задержкой' in narration
    assert 'резервным' in narration
    assert 'если таких путей мало' in narration
    assert not any(phrase in narration.lower() for phrase in ('всем привет', 'сегодня расскажу', 'подписывайся'))
    assert not any(char.isdigit() for char in narration)
    assert alternatives()['durations']['28'] == story.as_dict()


def test_exports_are_independent_and_bad_inputs_are_actionable():
    data = catalog()
    data[0]['hooks'].clear()
    assert len(catalog()[0]['hooks']) == 3
    plan = plan_for()
    plan['scenes'].clear()
    assert plan_for()['scenes']
    facts = facts_for('submarine-cables')
    facts[0]['sources'].clear()
    assert facts_for('submarine-cables')[0]['sources']
    for seconds in (19, 29, 41, True, 28.0, "28", None):
        with pytest.raises(ValueError, match='20, 28 and 40'):
            load_pilot(seconds=seconds)
    with pytest.raises(ValueError, match='No reviewed pilot'):
        load_pilot('not-a-topic')
    with pytest.raises(ValueError, match='Unknown topic_id'):
        facts_for('not-a-topic')


def test_missing_metrics_stay_missing_and_related_queries_are_labelled():
    research = json.loads(files('shorts_factory').joinpath('content','research.json').read_text())
    assert research['youtube']['windows'] == {'72h':None,'7d':None,'30d':None}
    assert research['tiktok']['metrics'] is None
    assert research['google_trends']['window'] == '24h'
    assert research['google_trends']['exact_pilot_query_metrics'] is None
    assert all(obs['limit'] for obs in research['google_trends']['observations'])
    assert not next(row for row in catalog() if row['id'] == 'submarine-cables')['popularity']
