import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from unittest.mock import patch, MagicMock
import json
import pytest

def test_call_anthropic_returns_text():
    """Groq API 호출이 텍스트를 반환하는지 확인"""
    from _alf_common import call_anthropic
    mock_response = json.dumps({
        "choices": [{"message": {"content": "테스트 응답"}}]
    }).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = call_anthropic("테스트 프롬프트", api_key="test-key")
    assert result == "테스트 응답"

def test_call_anthropic_raises_on_empty_key():
    """API 키 없으면 ValueError 발생"""
    from _alf_common import call_anthropic
    with pytest.raises(ValueError, match="api_key"):
        call_anthropic("프롬프트", api_key="")

def test_get_supabase_headers():
    """Supabase 헤더가 올바른 형식인지 확인"""
    from _alf_common import get_supabase_headers
    headers = get_supabase_headers("test-service-key")
    assert headers["apikey"] == "test-service-key"
    assert headers["Authorization"] == "Bearer test-service-key"
    assert headers["Content-Type"] == "application/json"

def test_supabase_get_calls_url():
    """supabase_get이 올바른 URL로 GET 요청 보내는지"""
    from _alf_common import supabase_get
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"id": "1"}]).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        result = supabase_get("https://example.supabase.co/rest/v1/table", "svc-key")
    assert result == [{"id": "1"}]
    assert mock_open.called

def test_supabase_post_empty_body_returns_empty_dict():
    """응답 body가 비어있으면 {} 반환"""
    from _alf_common import supabase_post
    mock_resp = MagicMock()
    mock_resp.read.return_value = b""
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = supabase_post("https://example.supabase.co/rest/v1/table", {"key": "val"}, "svc-key")
    assert result == {}

def test_make_handler_base_has_required_methods():
    """make_handler_base가 반환하는 클래스에 _respond, do_OPTIONS, log_message가 있는지"""
    from _alf_common import make_handler_base
    Base = make_handler_base()
    assert hasattr(Base, "_respond")
    assert hasattr(Base, "do_OPTIONS")
    assert hasattr(Base, "log_message")
