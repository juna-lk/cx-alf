import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from unittest.mock import patch, MagicMock
import json

def _make_chat(chat_id, tags=None, handling_type="agent", assignee_id="123", assignee_name="지영"):
    return {
        "id": chat_id,
        "tags": tags or ["플랜 환불"],
        "createdAt": 1747000000000,
        "handlingType": handling_type,
        "csatScore": 5,
        "assigneeId": assignee_id,
        "assigneeName": assignee_name,
    }

def _make_messages(texts):
    return [
        {"personType": "user" if i % 2 == 0 else "manager",
         "plainText": t, "createdAt": 1747000000000 + i * 1000}
        for i, t in enumerate(texts)
    ]

def test_parse_messages_extracts_roles():
    """메시지 파싱 시 role이 올바르게 변환되는지"""
    from alf_collect import parse_messages
    raw = [
        {"personType": "user", "plainText": "환불 해주세요", "createdAt": 1000},
        {"personType": "manager", "plainText": "네, 가능합니다.", "createdAt": 2000},
        {"personType": "bot", "plainText": "안녕하세요!", "createdAt": 3000},
    ]
    result = parse_messages(raw)
    assert result[0] == {"role": "customer", "text": "환불 해주세요"}
    assert result[1] == {"role": "agent", "text": "네, 가능합니다."}
    assert result[2] == {"role": "alf", "text": "안녕하세요!"}

def test_build_row_from_chat():
    """채팅 데이터에서 cx_full_messages row 생성"""
    from alf_collect import build_row
    chat = _make_chat("chat_001", tags=["플랜 환불"])
    messages = [{"role": "customer", "text": "환불"}, {"role": "agent", "text": "네"}]
    row = build_row(chat, messages)
    assert row["chat_id"] == "chat_001"
    assert row["handling_type"] == "agent"
    assert row["message_count"] == 2
    assert row["alf_tried"] is False
    assert row["assignee_name"] == "지영"

def test_build_row_detects_alf_tried():
    """ALF 메시지가 있으면 alf_tried=True"""
    from alf_collect import build_row
    chat = _make_chat("chat_002", handling_type="agent")
    messages = [
        {"role": "alf", "text": "안녕하세요"},
        {"role": "customer", "text": "환불"},
        {"role": "agent", "text": "네"},
    ]
    row = build_row(chat, messages)
    assert row["alf_tried"] is True
