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
- **futureschole-ai-all 듀얼 push 완료** — origin에 두 URL 등록, `git push origin main` 한 번으로 양쪽 자동 push
- LLM 모델 전환 검토 중 — gpt-oss vs Groq 비교 제공함 (비용·속도·품질 트레이드오프)

### Handoff Context (paste into next session)

**현재 상태:** ALF Studio (cx-alf) 기능 구현 완료. `juna-lk/cx-alf` + `futureschole-ai-all/cx-alf` 듀얼 push 설정 완료.

**열린 질문:** LLM 모델을 Groq → gpt-oss로 전환할지 검토 중. 사용자가 "gpt-oss"가 정확히 어떤 서비스/모델 ID를 말하는지 확인 필요.

**듀얼 push 현재 설정 (완료됨):**
```bash
# origin에 두 push URL 등록되어 있음 — 확인:
cd /Users/juna/cx-alf && git remote -v
# git push origin main 한 번으로 juna-lk + futureschole 동시 push
```

**LLM 전환 검토 시:**
- 현재: `api/_alf_common.py`의 `call_anthropic()` → Groq endpoint + `GROQ_API_KEY`
- 전환 시 변경 지점: endpoint URL, model 명, API key 환경변수만 교체하면 됨
- Groq 대비 gpt-oss 장점: 한국어 품질 안정성, JSON 출력 신뢰도, 높은 rate limit
- Groq 대비 단점: 유료 (토큰 과금), 속도 느림

**프로젝트 구조 핵심:**
- Vercel: `cx-alf` 프로젝트, URL `claude-juna.vercel.app`
- Supabase: `axqrhnfmrqwdmebjmsrh.supabase.co` Team 플랜
- LLM: Groq llama-3.3-70b-versatile (GROQ_API_KEY), TPM 6,000 한도
- 인증: Google OAuth + @liveklass.com 도메인 제한
- 데이터 자동 수집: GitHub Actions 매일 03:00 KST

**다음 작업 후보:**
1. (결정 대기) gpt-oss 전환 — 모델 ID 확인 후 `_alf_common.py` endpoint/model 교체
2. (선택) 아티클 + 부속 FAQ 통합 생성 모드
3. 사용하면서 발견되는 톤/구조 이슈 보완

---

## Handoff: 2026-05-19T08:30:00Z

### Current Task State
ALF Studio (cx-alf) 기능 개선 작업 완료. 계정 전환 전 저장 요청.

### Key Decisions
- **LLM**: Gemini 2.0 Flash → **gpt-4o-mini** 전환 (RPM 500, 429 문제 해소)
- **샘플 크기**: 15건 → **50건** 확대 (gpt-4o-mini TPM 여유 있음)
- **환경변수**: `GEMINI_API_KEY` → `OPENAI_API_KEY` (Vercel에 설정 완료)
- **검증 오탐 3건 수정**: 종결형 비율(헤딩/불릿 제외), 소제목(300자 미만 제외), 드리겠습니다(도입부 패턴만)
- **cx_full_messages**: `url` 컬럼 추가, `--all` 모드로 26,282건 전체 백필 진행 중

### Modified Files
- `api/_alf_common.py` — LLM OpenAI 전환, 검증 오탐 수정, 429 재시도, JSON 에러 핸들링
- `api/alf_collect.py` — `build_row()`에 `url` 필드 추가
- `api/alf_generate.py`, `alf_faq_generate.py`, `alf_format_suggest.py`, `alf_search.py` 등 8개 — `OPENAI_API_KEY` + 샘플 50건
- `alf-draft/index.html` — 태그 × 초기화 버튼, 4단계 진행 표시, 순차 실행
- `run_collect_full.py` — `--all` 모드 추가

### Blockers / Open Questions
- `run_collect_full.py --all` 백필 26,282건 진행 중. 완료 여부 미확인.
- cx-tag-dashboard Vercel `GEMINI_API_KEY` 제거 필요 (선택)

### Next Steps
1. 백필 완료 확인 (터미널 `[완료]` 메시지)
2. ALF Studio 전체 테스트 (태그 분석 → 아티클/FAQ 생성)
3. (선택) 아티클 + 부속 FAQ 통합 생성 모드 구현

### Critical Context
- 듀얼 push 완료: `git push origin main` 한 번으로 juna-lk + futureschole-ai-all 동시 push
- gpt-4o-mini: `$10` 크레딧, 하루 수십 회 분석해도 약 $0.15/일
- 샘플 50건 기준 토큰 ~5,500/요청 → TPM 여유 있음
- cx_full_messages `url` 컬럼: Supabase에서 사용자가 직접 추가 완료

### Model Summary
- ALF Studio LLM: gpt-4o-mini 최종 확정 (429 문제 해소, 한국어 품질 안정)
- 분석 샘플: 15건 → 50건 확대
- cx_full_messages: url 컬럼 추가 + 26,282건 전체 백필 진행 중
- 태그 선택 UX: × 초기화 버튼 + 4단계 진행 표시 + 순차 실행
- 검증 오탐 3건 수정 (종결형 비율·소제목·드리겠습니다)
- 모든 변경 commit+push 완료 (juna-lk + futureschole-ai-all)

### Handoff Context (paste into next session)

**현재 상태:** ALF Studio (cx-alf) 기능 개선 완료. gpt-4o-mini 전환, 샘플 50건, 검증 오탐 수정.

**다음 세션 시작 시:**
1. 백필 완료 확인 → Supabase `cx_full_messages` 건수 확인
2. claude-juna.vercel.app 접속 → 태그 분석 → 아티클 생성 전체 테스트
3. (선택) 아티클 + FAQ 통합 생성 모드 추가

**최신 커밋:** `b8a69ad fix: 검증 오탐 개선`
**듀얼 push:** `git push origin main` 한 번으로 양쪽 자동 push

---
