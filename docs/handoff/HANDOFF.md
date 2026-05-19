# cx-alf 작업 핸드오프

---

## Handoff: 2026-05-19T11:00:00Z

### Current Task State
ALF Studio (cx-alf) 웹앱 개발 진행 중. 채널톡 ALF용 CX 상담 데이터 기반 지식 초안(아티클·FAQ·규칙) 자동 생성 도구.
주요 기능·UI·자동화·검증 시스템 모두 구현 완료. 현재 **futureschole-ai-all 듀얼 push 설정 직전**에 작업 중단됨 (사용자 요청).

### Key Decisions

#### 인증 & 보안
- **Google OAuth (Supabase) + @liveklass.com 도메인 제한**: cx-dashboard와 동일한 방식. 외부 접근 완전 차단.
- 모든 API 엔드포인트: `_check_auth()` → Supabase `/auth/v1/user`로 토큰 검증 + 이메일 도메인 확인
- 프론트엔드: `_supa.auth.signInWithOAuth({provider:'google'})` 사용, 세션 토큰을 `authFetch`로 모든 API 호출에 자동 첨부
- 환경변수: `SUPABASE_ANON_KEY` 필수 (없으면 인증 비활성화 — 개발용 fallback)

#### LLM
- **Groq llama-3.3-70b-versatile (무료 티어)**: TPM 6,000 / TPD 500,000
- Cloudflare 1010 차단 회피: User-Agent 헤더 필수 (`cx-alf/1.0 (+...)`)
- 출력 후처리: `sanitize_korean` (한자→한글), `strip_article_boilerplate` (인사·자기소개·마무리 제거)
- 샘플 크기: 15건 (Groq TPM 한도 때문에 40→15로 축소 — 토큰 ~4000 안전 범위)

#### 콘텐츠 톤
- 채널톡 Help Doc 표준 톤: `-어요/-습니다` 친근한 존댓말
- 금지: 자기소개, 마무리 인사("감사합니다"), 직접 질문, 이모지, "보통/일반적으로"
- 1:1 상담 응답 어투 → 일반 안내 어투로 자동 변환

#### 자동 검증
- `verify_draft(content, format, cluster_label)` — 8가지 규칙 자동 체크 + 이모지 자동 제거
- 결과 배너로 통과/이슈 표시 (error/warning/info 3단계)

### Modified Files

#### API (백엔드)
- `api/_alf_common.py` — Groq 호출, Supabase REST, 인증, 후처리 (sanitize_korean, strip_article_boilerplate, verify_draft, extract_json)
- `api/alf_data.py` — 모든 데이터 읽기·쓰기 통합 API (drafts/rules/tags/stats)
- `api/alf_search.py` — 태그·키워드 검색 + LLM 클러스터링 (chat_indices 매핑)
- `api/alf_generate.py` — 아티클 생성 (Help Doc 톤 + 후처리 + 검증)
- `api/alf_faq_generate.py` — FAQ 생성 (질문·변형·답변 JSON)
- `api/alf_format_suggest.py` — FAQ vs 아티클 추천
- `api/alf_structure_suggest.py` — 기존 문서 추가 vs 신규 작성 추천
- `api/alf_merge_suggest.py`, `api/alf_merge.py` — 적용 가이드 병합 분석·실행 (소스 삭제 포함)
- `api/alf_rule_suggest.py` — 규칙 4종 추천 (disambiguation/tone/terminology/priority)
- `api/alf_collect.py` — 채널톡 → cx_full_messages 동기화

#### 프론트엔드
- `alf-draft/index.html` (~2500줄) — 단일 페이지 앱. 5개 탭: 초안 생성 / 작성 중인 초안 / ALF 적용 가이드 / ALF 규칙 / 작성 가이드
  - Supabase JS SDK + Google OAuth 로그인
  - Canvas 이미지 자동 스타일링 (W1500/라운드25/그림자/검정 1px) + Supabase Storage 업로드
  - 가이드 탭: 사이드바 목차 + 4컬럼 가로 파이프라인 다이어그램

#### 자동화·인프라
- `.github/workflows/alf-collect.yml` — 매일 KST 03:00 cron으로 7일치 신규 상담 수집
- `run_collect_full.py` — 채널톡 채팅 백필 (cx_chats 기반 누락 탐지 + upsert)
- `vercel.json` — 10개 API 함수 등록
- `.env` (gitignored) — 로컬 개발용 키 값들

### Blockers / Open Questions
- **futureschole-ai-all/cx-alf 듀얼 push 미설정** — 사용자 요청 후 작업 중단됨. 다음 세션에서 진행 예정.
- Groq TPM 6,000 한도 때문에 샘플 15건으로 제한. 더 풍부한 분석 필요 시 Groq Pro 또는 Gemini 전환 검토.

### Next Steps
1. **futureschole-ai-all/cx-alf 듀얼 push 설정**
   - GitHub에서 `futureschole-ai-all/cx-alf` 레포 생성 필요 (사용자 작업)
   - 로컬 remote 추가: `git remote add futureschole https://github.com/futureschole-ai-all/cx-alf.git`
   - 푸시 순서: `git push origin main && git push futureschole main`
   - Vercel·Supabase는 `juna-lk/cx-alf`에 연동되어 있으므로 `origin` 유지
2. (선택) 아티클 + 부속 FAQ 통합 생성 기능 — 사용자가 이전에 동의한 기능 (구현 필요)
3. (선택) 규칙 예시 라이브러리 확장
4. 사용해보면서 발견되는 톤/구조 이슈 후처리 보완

### Critical Context

#### 레포·배포
- **GitHub**: `juna-lk/cx-alf` (단일 origin)
- **Vercel**: 프로젝트명 `cx-alf`, 도메인 `claude-juna.vercel.app` (이름만 변경됨), GitHub의 `juna-lk/cx-alf` 자동 배포
- **Supabase**: 프로젝트 `axqrhnfmrqwdmebjmsrh.supabase.co` (cx-dashboard와 공유), Team 플랜

#### Supabase 테이블·버킷
- `cx_full_messages` — chat_id PK, tags TEXT[], messages JSONB. 약 2,800건 (138일치)
- `alf_drafts` — id UUID, title, content, format(article/faq), variations JSONB, status(draft/applied)
- `alf_rules` — id UUID, title, content, rule_type, related_draft_ids UUID[]
- `cx_chats` — 기존 cx-dashboard 테이블. 태그 자동완성·백필에 활용
- Storage: `alf-images` 버킷 (public 읽기, authenticated @liveklass.com 쓰기)

#### Vercel 환경변수
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` — 백엔드 DB 접근
- `SUPABASE_ANON_KEY` — 인증 검증 (필수)
- `ALLOWED_EMAIL_DOMAIN` — `liveklass.com`
- `GROQ_API_KEY` — LLM 호출
- `CHANNELTALK_ACCESS_KEY`, `CHANNELTALK_ACCESS_SECRET` — 채널톡 API

#### GitHub Secrets (Actions용)
- `CHANNELTALK_ACCESS_KEY/SECRET`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`

#### 코드 패턴
- 모든 do_POST/do_GET 첫 줄: `if not self._check_auth(): return`
- 모든 fetch (프론트): `authFetch(url, options)` 사용 (직접 Supabase 호출 금지)
- 파일 업로드: `_supa.storage.from('alf-images').upload(filename, blob)`
- 한국어 강제: 모든 LLM 시스템 프롬프트에 "일본어/한자/영어 금지" 명시 + 후처리

#### 채널톡 가이드라인 (반드시 따를 것)
- ALF는 **제목·소제목** 우선 검색 → 핵심 키워드 포함
- 본문에만 키워드 있으면 매칭 실패 가능
- 이미지만 있으면 ALF가 못 봄 → 이미지 위아래에 설명 텍스트 + 캡션 상세 작성
- 불릿/번호 목록으로 정보 선후 관계
- 완성된 종결형 `-어요/-습니다`
- 중복 문서 정리 (일관성·정확도 영향)

### Model Summary
- ALF Studio (cx-alf) — CX 상담 데이터로 채널톡 ALF용 아티클·FAQ·규칙 자동 생성하는 웹앱
- 5개 탭 SPA + 10개 Vercel Python serverless API + Supabase + Groq LLM 구조
- Google OAuth (@liveklass.com만) + Supabase 세션 토큰 기반 백엔드 인증
- 채널톡 Help Doc 톤 가이드라인 강제 + 자동 후처리 (보일러플레이트 제거 / 한자 변환 / 8가지 규칙 검증)
- 이미지 캡쳐→붙여넣기 → Canvas 자동 스타일링 (W1500 라운드/그림자/스트로크) → Supabase Storage 업로드
- GitHub Actions 매일 03:00 KST cron으로 채널톡 신규 상담 자동 수집
- 가이드 탭에 사이드바 목차 + 4컬럼 가로 파이프라인 다이어그램 + 채널톡 CS 답변 3건 원문 통합
- Groq 무료 티어 TPM 6000 한도 때문에 샘플 크기 15건으로 조정 (token ~4000)
- 사용자 요청으로 futureschole-ai-all 듀얼 push 설정 작업 중 중단됨

### Handoff Context (paste into next session)

**현재 작업:** ALF Studio (cx-alf) — `juna-lk/cx-alf` 단일 푸시 중.
**중단된 작업:** futureschole-ai-all/cx-alf 듀얼 푸시 설정 (사용자가 다른 우선 요청).

**바로 이어 진행하려면:**
1. 사용자에게 `futureschole-ai-all/cx-alf` GitHub 레포가 만들어졌는지 확인
2. 만들어졌으면:
   ```bash
   cd /Users/juna/cx-alf
   git remote add futureschole https://github.com/futureschole-ai-all/cx-alf.git
   git push futureschole main
   ```
3. 이후 모든 푸시는 `git push origin main && git push futureschole main` (둘 다)

**중요 제약:**
- Vercel·Supabase는 `juna-lk/cx-alf` 연동이라 `origin`을 그대로 유지
- 푸시 순서: origin(juna-lk) → futureschole 순으로
- 추후 cx-tag-dashboard처럼 자동 듀얼 푸시 alias 만들거나 git hook으로 처리 가능

**프로젝트 구조 핵심:**
- Vercel: `cx-alf` 프로젝트, URL `claude-juna.vercel.app`
- Supabase: `axqrhnfmrqwdmebjmsrh.supabase.co` Team 플랜
- LLM: Groq llama-3.3-70b-versatile, TPM 6,000 한도
- 인증: Google OAuth + @liveklass.com 도메인 제한
- 데이터 자동 수집: GitHub Actions 매일 03:00 KST

**최근 작업 마무리:**
- 헤더·탭 정렬 안정화 (flex-wrap, overflow-x:auto)
- 가이드 탭에 작동 방식 4컬럼 파이프라인 추가
- 사이드바 목차 → 시스템 그룹 추가됨

**Git 마지막 상태:**
- 최신 커밋: `b6f5e8c ui: 헤더·탭 정렬 안정화 (반응형 보강)`
- `git push origin main` 완료, `futureschole` remote 미설정

---
