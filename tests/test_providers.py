import httpx
import pytest

from shorts_factory import providers


def test_provider_json_errors_are_actionable():
    with pytest.raises(RuntimeError, match="malformed JSON"):
        providers._object(httpx.Response(200, content=b"not JSON"))
    with pytest.raises(RuntimeError, match="non-object"):
        providers._object(httpx.Response(200, json=[]))


def test_provider_error_omits_response_body_and_secrets(monkeypatch):
    def fail(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(429, request=request, text="private payload and secret-key")
    monkeypatch.setattr(httpx.Client, "post", fail)
    with pytest.raises(RuntimeError) as error:
        providers._request("https://api.openai.com/v1/responses", headers={}, json={})
    assert "HTTP 429" in str(error.value)
    assert "secret-key" not in str(error.value)
