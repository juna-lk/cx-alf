"""_pii.py 회귀 테스트.

가리는 것만큼이나 '안 가려야 할 것을 안 가리는지'가 중요하다. epoch 밀리초를
주민번호로 오인해 타임스탬프를 통째로 날릴 뻔한 적이 있어서, 그 경우를 못
박아 둔다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))

from _pii import (  # noqa: E402
    mask_name, mask_email, mask_phone, mask_text, mask_deep, desk_url,
)


# ── 가려야 하는 것 ────────────────────────────────────────────────────────────

def test_name_keeps_first_letter():
    assert mask_name("김철수") == "김**"
    assert mask_name("만") == "만*"
    assert mask_name("") == ""
    assert mask_name(None) is None


def test_email_keeps_shape():
    assert mask_email("hong.gildong@naver.com") == "h***@n***.com"


def test_phone_keeps_last_four():
    assert mask_phone("01012345678") == "010-****-5678"
    assert mask_phone("010-9876-5432") == "010-****-5432"


def test_text_masks_each_kind():
    out = mask_text(
        "계정 kim.abc@naver.com, 연락처 010-1234-5678, 계좌 110-234-567890, "
        "주민 900101-1234567, 사업자 123-45-67890, 예금주: 홍길동"
    )
    assert "kim.abc@naver.com" not in out
    assert "010-1234-5678" not in out
    assert "[ACCOUNT_REMOVED]" in out
    assert "[RRN_REMOVED]" in out
    assert "[BIZNO_REMOVED]" in out
    assert "[NAME_REMOVED]" in out


def test_rrn_without_separator_needs_label():
    assert "[RRN_REMOVED]" in mask_text("주민번호 9001011234567 입니다")


# ── 건드리면 안 되는 것 ───────────────────────────────────────────────────────

def test_epoch_milliseconds_survive():
    """13자리 epoch 는 주민번호 형태와 자릿수가 같다. 절대 가리면 안 된다."""
    for epoch in ("1750000000000", "1688002234000", "1789544027546"):
        assert mask_text(epoch) == epoch, f"{epoch} 가 마스킹됨"


def test_epoch_inside_json_string_survives():
    profile = {"last_login": "1688002234000", "plan_name": "프로"}
    assert mask_deep(profile)["last_login"] == "1688002234000"


def test_business_fields_survive():
    profile = {
        "name": "김철수",
        "site_name": "철수네클래스",
        "site_url": "https://cs.liveklass.com",
        "plan_name": "프로",
        "monthly_sales": 1200000,
    }
    out = mask_deep(profile)
    assert out["name"] == "김**"
    assert out["site_name"] == "철수네클래스"
    assert out["site_url"] == "https://cs.liveklass.com"
    assert out["monthly_sales"] == 1200000


def test_dates_and_amounts_survive():
    assert mask_text("2026-09-21 결제 49,900원") == "2026-09-21 결제 49,900원"


def test_order_and_chat_ids_survive():
    assert mask_text("주문번호 3258614") == "주문번호 3258614"
    assert mask_text("chat_id 6a7bcd1234ef") == "chat_id 6a7bcd1234ef"


def test_desk_url_has_no_name():
    assert desk_url("6a7b") == "https://desk.channel.io/liveklass/user-chats/6a7b"


def test_mask_deep_is_idempotent():
    """두 번 돌려도 같아야 백필을 다시 돌려도 안전하다."""
    row = {"name": "김철수", "email": "a@b.com", "note": "연락처 010-1234-5678"}
    once = mask_deep(row)
    assert mask_deep(once) == once


def test_name_label_skips_ui_copy():
    """가이드·UI 문구의 '이름:' 은 사람 이름이 아니다."""
    for s in ("표시 이름 : 방문자가 Facebook 로그인 시 보게 될 이름입니다",
              "이름: 판매 조건 이름을 입력해요",
              "페이지 이름: 운영 중인 메인 페이지 이름이 표기됩니다"):
        assert mask_text(s) == s, s


def test_name_label_masks_real_name():
    assert "[NAME_REMOVED]" in mask_text("예금주: 홍길동")
    assert "[NAME_REMOVED]" in mask_text("성함 : 김철수")


def test_phone_not_swallowed_by_account_pattern():
    """전화번호를 가린 결과가 계좌 패턴에 다시 걸려 뭉개지면 안 된다."""
    assert mask_text("연락처 010-1234-5678") == "연락처 010-****-5678"


def test_attachment_filenames_survive():
    """첨부의 name 은 파일명이지 사람 이름이 아니다. 가리면 데이터가 망가진다."""
    msgs = [{"role": "customer", "text": "파일 보냅니다",
             "attachments": [{"type": "image", "name": "image.png", "size": 100},
                             {"type": "file", "name": "환불요청서.pdf", "size": 200}]}]
    out = mask_deep(msgs)
    got = [a["name"] for a in out[0]["attachments"]]
    assert got == ["image.png", "환불요청서.pdf"], got


def test_profile_name_still_masked():
    """반면 profile 의 name 은 사람 이름이라 계속 가려야 한다."""
    assert mask_deep({"profile": {"name": "김철수"}})["profile"]["name"] == "김**"
    assert mask_deep({"name": "김철수"})["name"] == "김**"


def test_filename_keeps_everything_but_the_name():
    msgs = [{"attachments": [
        {"name": "이광규님_부분환불.pdf"},
        {"name": "2024-01-20 21:00:06.png"},
        {"name": "image.png"},
        {"name": "고객님_안내문.pdf"},
    ]}]
    got = [a["name"] for a in mask_deep(msgs)[0]["attachments"]]
    assert got == ["이**님_부분환불.pdf", "2024-01-20 21:00:06.png",
                   "image.png", "고객님_안내문.pdf"], got


def test_honorific_name_in_body_masked():
    assert mask_text("이광규님 환불 처리했습니다") == "이**님 환불 처리했습니다"


def test_honorific_titles_survive():
    for s in ("고객님 안녕하세요", "선생님께 전달했습니다", "회원님 계정입니다",
              "학부모님 문의", "담당자님 확인 부탁드립니다"):
        assert mask_text(s) == s, s


def test_internal_staff_names_unified():
    """CX 셀 인원 이름은 한 값으로 통일한다. 담당자별 구분은 포기."""
    from _pii import mask_internal, INTERNAL_LABEL
    assert mask_internal("전준호") == INTERNAL_LABEL
    assert mask_internal("Logan") == INTERNAL_LABEL
    assert mask_internal("<@U12345>") == INTERNAL_LABEL
    assert mask_internal("") == ""
    assert mask_internal(None) is None


def test_manager_key_in_messages_is_internal():
    msgs = [{"role": "agent", "text": "확인했습니다", "manager": "전준호"}]
    assert mask_deep(msgs)[0]["manager"] == "라클멤버"
    # 본문은 그대로
    assert mask_deep(msgs)[0]["text"] == "확인했습니다"


def test_email_in_name_field_is_masked():
    """이름 칸에 이메일을 적는 사람이 있다. `.com` 이 파일명 예외에 걸려
    그대로 통과하던 자리라 못 박아 둔다."""
    assert mask_name("iamgyuweon@gmail.com") == "i***@g***.com"
    assert mask_name("heossu@naver.com") == "h***@n***.com"
    # 진짜 파일명은 여전히 그대로
    assert mask_name("image.png") == "image.png"
