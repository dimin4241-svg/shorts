"""Azure contract and failure tests; all requests are local mocks, never billed."""
import xml.etree.ElementTree as ET

import httpx
import pytest

from shorts_factory import azure_speech

# MPEG-1 Layer III header, followed by fake payload for format-sniffing tests.
MP3 = b'\xff\xfb\x94\xc0' + b'\x00' * 100
NS = {'s': 'http://www.w3.org/2001/10/synthesis'}


@pytest.fixture
def azure_request(monkeypatch):
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'local-test-secret')
    monkeypatch.setenv('AZURE_SPEECH_REGION', 'westeurope')
    recorded = {'calls': 0, 'status': 200, 'body': MP3, 'type': 'audio/mpeg'}

    class Client:
        def __init__(self, **kwargs):
            recorded['client_options'] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            recorded['calls'] += 1
            recorded.update(url=url, request_options=kwargs)
            if recorded.get('error'):
                raise recorded['error']
            return httpx.Response(recorded['status'], content=recorded['body'],
                                  headers={'Content-Type': recorded['type']},
                                  request=httpx.Request('POST', url))

    monkeypatch.setattr(azure_speech.httpx, 'Client', Client)
    monkeypatch.setattr(azure_speech.httpx, 'HTTPTransport', lambda **kwargs: ('transport', kwargs))
    return recorded


def test_exact_official_request_escaped_ssml_and_no_inferred_style(azure_request, tmp_path):
    text = 'Он идёт & говорит: <break time="9s"/> «Привет».'
    target = tmp_path / 'voice.mp3'
    azure_speech.synthesize_azure(text, target, speed=1.04, delivery='storytelling')
    assert target.read_bytes() == MP3
    assert azure_request['calls'] == 1
    assert azure_request['url'] == 'https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1'
    args = azure_request['request_options']
    assert args['headers'] == {
        'Ocp-Apim-Subscription-Key': 'local-test-secret', 'Content-Type': 'application/ssml+xml',
        'X-Microsoft-OutputFormat': 'audio-48khz-192kbitrate-mono-mp3', 'User-Agent': 'shorts-factory',
    }
    root = ET.fromstring(args['content'])
    assert root.attrib['{http://www.w3.org/XML/1998/namespace}lang'] == 'ru-RU'
    voice = root.find('s:voice', NS)
    assert voice.attrib == {'name': 'ru-RU-DmitryNeural'}
    prosody = voice.find('s:prosody', NS)
    assert prosody.attrib == {'rate': '+4.00%'}
    assert prosody.text == text
    assert len(list(root.iter())) == 3
    assert 'express-as' not in args['content'].decode()
    options = azure_request['client_options']
    assert options['follow_redirects'] is False
    assert options['timeout'].connect == 15 and options['timeout'].read == 120
    assert options['transport'] == ('transport', {'retries': 0})


@pytest.mark.parametrize('speed,rate', [(0.75,'-25.00%'),(1,'+0.00%'),(1.35,'+35.00%')])
def test_relative_speed_percent_mapping(azure_request, tmp_path, speed, rate):
    azure_speech.synthesize_azure('Свет передаёт данные.', tmp_path/'v.mp3', speed=speed)
    root = ET.fromstring(azure_request['request_options']['content'])
    assert root.find('.//s:prosody', NS).attrib['rate'] == rate


@pytest.mark.parametrize('delivery',['natural','energetic','storytelling'])
def test_delivery_does_not_invent_voice_specific_emotion(azure_request, tmp_path, delivery):
    azure_speech.synthesize_azure('Привет.',tmp_path/'v.mp3',delivery=delivery,voice='ru-RU-SvetlanaNeural')
    ssml = azure_request['request_options']['content'].decode()
    assert 'ru-RU-SvetlanaNeural' in ssml
    assert 'style=' not in ssml and 'pitch=' not in ssml and 'mstts:' not in ssml


@pytest.mark.parametrize('overrides', [
    {'voice':'ru-RU-DmitryNeural"/><audio src="https://bad.invalid"/>'},
    {'voice':'en-US-JennyNeural'}, {'language':'en'}, {'speed':float('nan')},
    {'speed':float('inf')}, {'speed':True}, {'speed':0.7}, {'speed':1.36},
    {'delivery':'shouting'}, {'text':'\x00test'}, {'text':'\ud800'}, {'text':' '*3},
])
def test_invalid_arguments_fail_before_network(azure_request,tmp_path,overrides):
    args={'text':'Обычный текст.', 'destination':tmp_path/'v.mp3', **overrides}
    with pytest.raises(ValueError): azure_speech.synthesize_azure(**args)
    assert azure_request['calls']==0


@pytest.mark.parametrize('region',['https://bad.invalid','eastus.bad.invalid','usgovvirginia','region/key',''])
def test_invalid_region_cannot_change_destination(azure_request,monkeypatch,tmp_path,region):
    monkeypatch.setenv('AZURE_SPEECH_REGION',region)
    with pytest.raises((ValueError,RuntimeError)): azure_speech.synthesize_azure('Текст.',tmp_path/'v.mp3')
    assert azure_request['calls']==0


def test_missing_key_has_actionable_message_without_network(azure_request,monkeypatch,tmp_path):
    monkeypatch.delenv('AZURE_SPEECH_KEY')
    with pytest.raises(RuntimeError,match='AZURE_SPEECH_KEY missing'):
        azure_speech.synthesize_azure('Текст.',tmp_path/'v.mp3')
    assert azure_request['calls']==0


@pytest.mark.parametrize('status',[301,400,401,403,429,500])
def test_http_errors_are_sanitized_without_retry_or_overwrite(azure_request,tmp_path,status):
    azure_request.update(status=status,body=b'local-test-secret provider details')
    target=tmp_path/'v.mp3'; target.write_bytes(b'existing-good-audio')
    with pytest.raises(RuntimeError,match=f'HTTP {status}') as caught:
        azure_speech.synthesize_azure('Текст.',target)
    assert 'local-test-secret' not in str(caught.value)
    assert azure_request['calls']==1
    assert target.read_bytes()==b'existing-good-audio'


def test_timeout_is_sanitized_and_not_retried(azure_request,tmp_path):
    azure_request['error']=httpx.ReadTimeout('local-test-secret exposed in provider error')
    with pytest.raises(RuntimeError,match='ReadTimeout') as caught:
        azure_speech.synthesize_azure('Текст.',tmp_path/'v.mp3')
    assert 'local-test-secret' not in str(caught.value)
    assert azure_request['calls']==1


@pytest.mark.parametrize('kind,body',[
    ('application/json',MP3),('audio/mpeg',b'{"error":"local-test-secret"}'),
    ('text/html',b'<html>oops</html>'),('audio/mpeg',b''),('audio/mpeg',b'ID3'),
    ('audio/mpeg',b'ID3\x04\x00\x00\x00\x00\x00\x00'),
])
def test_non_audio_and_malformed_payloads_never_written(azure_request,tmp_path,kind,body):
    azure_request.update(type=kind,body=body)
    target=tmp_path/'v.mp3'
    with pytest.raises(RuntimeError,match='audio') as caught:
        azure_speech.synthesize_azure('Текст.',target)
    assert 'local-test-secret' not in str(caught.value)
    assert not target.exists()


def test_valid_id3_mp3_and_content_type_parameters(azure_request,tmp_path):
    tagged=b'ID3\x04\x00\x00\x00\x00\x00\x00'+MP3
    azure_request.update(type='Audio/MPEG; charset=binary',body=tagged)
    target=tmp_path/'v.mp3'
    azure_speech.synthesize_azure('Текст.',target,language='ru-RU')
    assert target.read_bytes()==tagged
