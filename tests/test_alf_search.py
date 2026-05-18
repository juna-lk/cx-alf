import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
from unittest.mock import patch, MagicMock
import pytest

def test_filter_by_keyword():
    """키워드가 messages 안에 있는 채팅만 필터링"""
    from alf_search import filter_by_keyword
    chats = [
        {"chat_id": "a", "messages": [{"role": "customer", "text": "환불 해주세요"}]},
        {"chat_id": "b", "messages": [{"role": "customer", "text": "배송 문의입니다"}]},
    ]
    result = filter_by_keyword(chats, "환불")
    assert len(result) == 1
    assert result[0]["chat_id"] == "a"

def test_filter_by_keyword_empty_keyword():
    """키워드가 빈 문자열이면 전체 반환"""
    from alf_search import filter_by_keyword
    chats = [
        {"chat_id": "a", "messages": [{"role": "customer", "text": "환불"}]},
        {"chat_id": "b", "messages": [{"role": "customer", "text": "배송"}]},
    ]
    result = filter_by_keyword(chats, "")
    assert len(result) == 2

def test_build_cluster_prompt_contains_chats():
    """클러스터링 프롬프트에 채팅 내용이 포함되는지"""
    from alf_search import build_cluster_prompt
    chats = [
        {"chat_id": "a", "messages": [
            {"role": "customer", "text": "환불"},
            {"role": "agent", "text": "네 가능합니다"},
        ]}
    ]
    prompt, sample_indices = build_cluster_prompt(chats, "플랜 환불")
    assert "환불" in prompt
    assert "플랜 환불" in prompt
    assert "JSON" in prompt
    assert sample_indices == [0]
