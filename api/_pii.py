"""수집 단계 개인정보 마스킹.

채널톡에서 긁어온 값을 Supabase 에 넣기 전에 통과시킨다. 원본을 저장한 뒤
나중에 지우는 방식은 백업·복제본·git 이력에 계속 남아서, 애초에 들어가지
않게 막는다.

기준은 cx-analysis-data/scripts/lib/masking-rules.md 와 같게 맞췄다.
  이메일  j***@n***.com
  전화    010-****-1234
  이름    김**
  계좌·주민번호  [ACCOUNT_REMOVED] / [RRN_REMOVED]

운영자(사이트 주인) 정보와 수강생 정보를 구분하지 않고 똑같이 가린다.
상담 식별은 chat_id 로 하고, 사람 식별이 필요하면 user_id 를 쓴다.

주의: 이 파일을 고치면 claude-juna/cx-dashboard/pii_mask.py 도 같이 고쳐야
한다. 두 저장소가 같은 테이블에 쓰기 때문에 한쪽만 고치면 다시 샌다.

cx-alf/alf_pii.py 와 헷갈리지 말 것. 그쪽은 슬랙 리포트·LLM 프롬프트로
'나가는' 텍스트를 `(이메일)` 같은 딱지로 바꾸는 용도고, 이 파일은 Supabase 에
'들어가는' 값을 형태만 남기고 가리는 용도다. 저장본은 형태를 남겨야 나중에
집계·중복 판정이 되므로 딱지 방식을 쓰지 않는다.
"""
from __future__ import annotations
import re

EMAIL_RE = re.compile(r'\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9])[A-Za-z0-9.-]*\.([A-Za-z]{2,})\b')
PHONE_RE = re.compile(r'\b(01[016789])[-. ]?(\d{3,4})[-. ]?(\d{4})\b')
TEL_RE = re.compile(r'\b(0(?:2|[3-6][1-5]|70|80))[-. ](\d{3,4})[-. ](\d{4})\b')
ACCOUNT_RE = re.compile(r'\b\d{2,6}-\d{2,6}-\d{4,8}(?:-\d{1,3})?\b')
# 구분자를 반드시 요구한다. 하이픈 없는 13자리를 허용하면 epoch 밀리초
# (1750000000000 같은 값)가 전부 주민번호로 잡혀서 last_login·created_at 이
# 통째로 [RRN_REMOVED] 로 망가진다. 실제로 그 사고가 날 뻔했다.
RRN_RE = re.compile(r'(?<!\d)\d{6}[-\s][1-4]\d{6}(?!\d)')
# 구분자 없이 적은 주민번호는 앞에 붙은 낱말로만 판별한다.
RRN_LABELED_RE = re.compile(r'(주민(?:등록)?번호)\s*[:：]?\s*(\d{6}[-\s]?[1-4]\d{6})(?!\d)')
CARD_RE = re.compile(r'\b(?:\d{4}[- ]){3}\d{4}\b')
BIZ_RE = re.compile(r'\b\d{3}[-\s]\d{2}[-\s]\d{5}\b')
# "예금주: 홍길동" 처럼 라벨을 달고 적는 이름. 패턴만으로는 못 잡는 자리다.
NAME_LABEL_RE = re.compile(r'(성함|이름|예금주|담당자|신청자|가입자|수취인)\s*[:：]\s*([가-힣]{2,4})')
# 라벨 뒤에 늘 사람 이름이 오지는 않는다. 가이드·UI 문구("표시 이름: 방문자가
# 로그인 시…", "이름: 판매 조건 이름을 입력해요")까지 가리면 문서가 망가진다.
NAME_LABEL_STOPWORDS = {
    "방문자", "사용자", "고객님", "고객", "회원", "강사", "수강생", "관리자",
    "운영", "운영자", "판매", "결제", "주문", "상품", "클래스", "페이지",
    "사이트", "계정", "이메일", "연락처", "본인", "해당", "각각", "아래",
    "위와", "다음", "없음", "미기재", "미입력", "입력", "선택", "표시",
}

# user.profile 에서 사람을 가리키는 키. 값이 있으면 마스킹한다.
PROFILE_NAME_KEYS = {"name", "userName", "realName", "nickname", "ownerName", "ceoName"}
PROFILE_EMAIL_KEYS = {"email", "userEmail", "contactEmail", "signEmail"}
PROFILE_PHONE_KEYS = {"mobileNumber", "phone", "phoneNumber", "tel", "contactPhone"}
PROFILE_DROP_KEYS = {"address", "addressDetail", "zipCode", "bizRegNo", "accountNumber",
                     "residentNumber", "birthday", "birthDate"}
# CX 셀 내부 인원 이름. 고객 정보는 아니지만 이름은 이름이라 한 값으로 통일한다
# (2026-09-22 결정). 담당자별 분석이 안 되는 건 감수하기로 했다.
# 형태가 제각각이라(영문명·한글명·슬랙 멘션 `<@U...>`) 패턴으로는 못 가린다.
# 이 키들에는 내부 인원만 들어오므로 값이 있으면 통째로 바꾼다.
INTERNAL_LABEL = "라클멤버"
INTERNAL_NAME_KEYS = {"manager", "agent_name", "agentName", "assignee_name",
                      "assigneeName", "source_author", "submitted_by"}
# 이 안에서는 `name` 이 사람이 아니라 파일·항목 이름이다. 구분 안 하면
# 첨부 파일명 image.png 가 i**** 로 뭉개진다(실제로 그랬다).
NON_PERSON_CONTAINERS = {"attachments", "files", "buttons", "blocks", "options",
                         "workflow", "form", "meet", "tags"}
# 확장자가 붙은 값은 사람 이름이 아니다.
FILENAME_RE = re.compile(r'\.[A-Za-z0-9]{2,5}$')
# "홍길동님_부분환불.pdf" 처럼 '님' 을 붙여 쓴 이름. 상담 본문에도 흔하다.
HONORIFIC_RE = re.compile(r'(?<![가-힣])([가-힣]{2,4})님')
# '님' 이 붙어도 사람 이름이 아닌 말들.
HONORIFIC_STOPWORDS = {
    "고객", "선생", "회원", "사장", "대표", "부모", "학부모", "어머", "아버",
    "강사", "운영자", "담당자", "관리자", "수강생", "이용자", "여러분", "사용자",
    "구매자", "판매자", "작성자", "신청자", "가입자", "결제자", "주문자",
}


def mask_name(value):
    """김철수 → 김**,  Alice → A**.  빈 값은 그대로 None/''."""
    if not value:
        return value
    s = str(value).strip()
    if not s:
        return s
    if "*" in s:          # 이미 가려진 값 (김**)
        return s
    if FILENAME_RE.search(s):   # image.png, 환불요청서.pdf — 사람 아님
        return s
    if len(s) == 1:
        return s + "*"
    return s[0] + "*" * (len(s) - 1)


def mask_internal(value):
    """CX 셀 내부 인원 이름 → 라클멤버. 빈 값은 그대로."""
    return INTERNAL_LABEL if value else value


def is_masked(value) -> bool:
    """이미 가려진 값인지. 백필을 두 번 돌려도 안전해야 한다."""
    if not isinstance(value, str):
        return False
    return "***@" in value or "-****-" in value or "_REMOVED]" in value


def mask_email(value):
    if not value:
        return value
    s = str(value)
    if is_masked(s):
        return s
    masked = EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}***.{m.group(3)}", s)
    # 패턴에 안 맞는 이상값은 통째로 가린다 (일부만 남기면 새는 쪽이 더 위험)
    return masked if masked != s or "@" not in s else "[EMAIL_REMOVED]"


def mask_phone(value):
    if not value:
        return value
    s = str(value)
    s = PHONE_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    s = TEL_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    return s


def mask_text(value):
    """상담 본문처럼 자유 입력 문자열에서 개인정보 패턴만 골라 가린다."""
    if not value or not isinstance(value, str):
        return value
    # 순서가 중요하다. 주민·카드·사업자번호를 먼저 걷어내야 계좌 패턴이
    # 그 숫자들을 먼저 집어삼키지 않는다.
    s = RRN_LABELED_RE.sub(lambda m: f"{m.group(1)}: [RRN_REMOVED]", value)
    s = RRN_RE.sub("[RRN_REMOVED]", s)
    s = CARD_RE.sub("[CARD_REMOVED]", s)
    s = BIZ_RE.sub("[BIZNO_REMOVED]", s)
    s = EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}***.{m.group(3)}", s)
    s = PHONE_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    s = TEL_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    s = ACCOUNT_RE.sub("[ACCOUNT_REMOVED]", s)
    s = NAME_LABEL_RE.sub(_sub_name_label, s)
    s = HONORIFIC_RE.sub(_sub_honorific, s)
    return s


def _sub_honorific(m):
    """'홍길동님' → '이**님'. '고객님' 같은 호칭은 그대로."""
    word = m.group(1)
    if word in HONORIFIC_STOPWORDS or any(word.startswith(x) for x in HONORIFIC_STOPWORDS):
        return m.group(0)
    return f"{word[0]}{'*' * (len(word) - 1)}님"


def mask_filename(value):
    """파일명 전용. 이름만 가리고 날짜·문서 종류는 남긴다.

    `홍길동님_부분환불.pdf` → `이**님_부분환불.pdf`
    파일명에 본문 규칙을 통째로 적용하면 `2024-01-20 21:00:06.png` 같은 값이
    계좌·주민번호로 오인돼 망가진다.
    """
    if not value or not isinstance(value, str):
        return value
    s = HONORIFIC_RE.sub(_sub_honorific, value)
    s = EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}***.{m.group(3)}", s)
    s = PHONE_RE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    return s


def _sub_name_label(m):
    """라벨 뒤 낱말이 사람 이름일 때만 가린다.

    '방문자가', '판매 조건' 처럼 조사·수식어가 붙어 오므로 앞부분으로 따진다.
    구분자 주변 공백은 원문 그대로 둔다 (문서를 불필요하게 바꾸지 않으려고).
    """
    word = m.group(2)
    if any(word.startswith(stop) for stop in NAME_LABEL_STOPWORDS):
        return m.group(0)
    return m.group(0)[: -len(word)] + "[NAME_REMOVED]"


def mask_deep(value, container=None):
    """dict / list / str 를 재귀적으로 훑어 개인정보를 가린다.

    키 이름으로 이름·이메일·전화를 판별하고, 그 밖의 문자열은 본문 규칙을
    적용한다. 숫자·불린은 건드리지 않는다.

    `container` 는 지금 보고 있는 값이 어느 키 밑에 있는지다. 같은 `name` 이라도
    profile 밑이면 사람 이름이고 attachments 밑이면 파일명이라 구분이 필요하다.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in PROFILE_DROP_KEYS:
                continue
            person_ok = container not in NON_PERSON_CONTAINERS
            if k in INTERNAL_NAME_KEYS:
                out[k] = mask_internal(v) if isinstance(v, str) else mask_deep(v, k)
            elif k in PROFILE_NAME_KEYS and not person_ok and isinstance(v, str):
                # 첨부 파일명 등. 이름만 가리고 나머지는 남긴다.
                out[k] = mask_filename(v)
            elif k in PROFILE_NAME_KEYS and person_ok:
                out[k] = mask_name(v) if isinstance(v, str) else mask_deep(v, k)
            elif k in PROFILE_EMAIL_KEYS:
                out[k] = mask_email(v) if isinstance(v, str) else mask_deep(v, k)
            elif k in PROFILE_PHONE_KEYS:
                out[k] = mask_phone(v) if isinstance(v, str) else mask_deep(v, k)
            else:
                out[k] = mask_deep(v, k)
        return out
    if isinstance(value, list):
        return [mask_deep(v, container) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def mask_messages(messages):
    """cx_full_messages.messages 용. 본문·첨부 파일명까지 훑는다."""
    return mask_deep(messages or [])


def desk_url(chat_id: str, workspace: str = "liveklass") -> str:
    """상담 링크. 예전엔 `{고객이름}-{chat_id}` 형태라 URL 자체가 실명을
    흘렸다. chat_id 만으로도 채널톡에서 정상적으로 열린다."""
    return f"https://desk.channel.io/{workspace}/user-chats/{chat_id}"
