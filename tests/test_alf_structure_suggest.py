import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
import pytest

def test_build_suggest_prompt_includes_existing():
    """프롬프트에 기존 문서 목록이 포함되는지"""
    from alf_structure_suggest import build_suggest_prompt
    existing = [
        {"id": "uuid1", "title": "플랜 환불 가이드", "cluster_label": "구독 중 환불"},
        {"id": "uuid2", "title": "플랜 업그레이드", "cluster_label": "플랜 업그레이드"},
    ]
    prompt = build_suggest_prompt("환불 처리 기간", "플랜 환불", existing)
    assert "플랜 환불 가이드" in prompt
    assert "환불 처리 기간" in prompt  # cluster_label
    assert "플랜 환불" in prompt        # tag
    assert "JSON" in prompt

def test_build_suggest_prompt_no_existing():
    """기존 문서 없을 때 신규 생성 권장 프롬프트"""
    from alf_structure_suggest import build_suggest_prompt
    prompt = build_suggest_prompt("환불 처리 기간", "플랜 환불", [])
    assert "환불 처리 기간" in prompt  # cluster_label
    assert "플랜 환불" in prompt        # tag
    assert "기존 문서 없음" in prompt
