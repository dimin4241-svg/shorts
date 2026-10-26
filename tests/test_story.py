import json
import pytest
from shorts_factory.story import Scene, Story, demo_story, parse_story, story_prompt
from shorts_factory.visuals import validate_svg
from shorts_factory.render import _timestamp


def test_story_valid_json():
    payload = {"title": "Заголовок", "description": "описание", "scenes": [
        {"narration": "Текст", "caption": "Подпись", "image_prompt": "shape"} for _ in range(3)]}
    story = parse_story("```json\n" + json.dumps(payload) + "\n```")
    assert len(story.scenes) == 3
    assert story.title == "Заголовок"


def test_reject_bad_scene_count():
    with pytest.raises(ValueError):
        parse_story(json.dumps({"title": "X", "description": "X", "scenes": []}))


def test_reject_xss_svg():
    with pytest.raises(ValueError):
        validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(ValueError):
        validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/test.png" /></svg>')
    with pytest.raises(ValueError):
        validate_svg('<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://example.org)" /></svg>')


def test_accept_clean_svg():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1536" viewBox="0 0 1024 1536"><rect x="0" y="0" width="1024" height="1536" fill="#fff"/><circle cx="100" cy="100" r="50" fill="#aff"/></svg>'
    assert validate_svg(svg) == svg


def test_different_variant_in_prompt():
    assert "VARIANT: 2" in story_prompt("Earth", "ru", 2, "whiteboard", 30)


def test_timestamp():
    assert _timestamp(61.25) == "00:01:01,250"


def test_demo_story():
    assert len(demo_story("test").scenes) == 4
