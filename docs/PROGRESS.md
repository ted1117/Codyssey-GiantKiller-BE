# Progress: PRD-001 회원·가족·초대

Last updated: 2026-10-30 18:49 KST

## Goal

- FastAPI Users 계정과 가족·초대를 연결한다.
- 이번 브랜치의 DB 모델·레포지토리·마이그레이션, API 스키마, 인증·서비스·엔드포인트를 완료했다.

## Current Status

- Status: Ready for review (3단계)
- Current focus: 회원·가족·초대 전체 구현 검토
- Branch: `feature/user`
- Related issue/PR: 없음

## Decisions

- 2026-10-30: 사용자가 PostgreSQL 유지를 확인했다. AGENTS.md의 SQLite 기술 스택은 적용하지 않는다.
- 2026-10-30: Python은 기존 프로젝트 설정인 3.14를 유지했다.
- 2026-10-30: FastAPI Users 어댑터와 호환되는 SQLAlchemy 2.0.55를 사용한다.
- 2026-10-30: 가족·멤버·초대 레포지토리는 DB 접근과 flush만 담당한다. 서비스에서 정책과 트랜잭션을 조합한다.
- 2026-10-30: FCM·기기 등록과 사칭 확인 응답 정책은 이번 작업에서 제외한다.
- 2026-10-30: 인증 기본값은 Bearer JWT·3600초다. 갱신·서버 측 폐기·로그아웃 API는 추가하지 않는다.
- 2026-10-30: 가족별 역할 겸용은 허용하고 계정 전체의 역할 제한은 두지 않는다.
- 2026-10-30: 초대 재취소는 204, 사용 완료는 409, 만료·취소 초대 수락은 410으로 처리한다.
- 2026-10-30: 미설정 초대 주소는 API 미리보기 URL로 반환하고, 앱·웹 링크 prefix는 환경변수로 설정한다.

## Completed

- [x] FastAPI Users 15.0.5와 SQLAlchemy 어댑터 7.0.0 설치
- [x] User, Family, FamilyMember, FamilyInvitation 모델
- [x] 계정 어댑터, 가족·멤버·초대 저장·조회 레포지토리
- [x] 중복 소속, 가족별 대표 유일성, 대표 역할, 초대 이력의 DB 제약
- [x] 가족·초대 행 잠금 조회, 멤버 이름의 명시적 eager loading
- [x] Alembic 설정과 초기 마이그레이션
- [x] 분리된 PostgreSQL 18에서 통합 테스트 15개 통과
- [x] SPEC에 구현 상태와 서비스 단계의 책임 기록
- [x] 가입·사용자 조회, 가족·멤버 조회, 초대 생성·미리보기 요청·응답 스키마
- [x] 이름 1~100자·이메일·초대 역할 검증, 서버 지정 필드 입력 거부
- [x] 응답의 비밀번호·토큰 해시 제외와 실제 ORM 멤버 이름 변환
- [x] 스키마 검증 19개 추가, PostgreSQL 18에서 전체 34개 통과
- [x] FastAPI Users 가입·로그인과 `/users/me`, JWT 인증 연결
- [x] 일반 가입 권한 입력 무시·비밀번호 해시·동시 중복 가입 처리
- [x] 대표 자녀의 가족 생성, 가족별 목록·상세·권한 검사
- [x] 대표의 초대 생성·취소, 로그인 사용자의 미리보기·수락
- [x] 초대 24시간·1회 사용, 부모 최대 2명, 다중 가족 참여
- [x] 가족 → 초대 잠금 순서와 최신 상태 확인, 부분 쓰기 롤백
- [x] 인증 실패·권한 거부 로그와 토큰 원문 없는 access log 실행 설정
- [x] PostgreSQL 18에서 전체 49개 테스트 통과
- [x] Docker 이미지 빌드, 이미지 내 마이그레이션, 앱 시작·가입·로그인·가족·초대 실행
- [x] Compose 설정 검증과 README 실행·설정 안내

## In Progress

- 없음. 3단계 구현·검증 완료.

## Next Steps

1. 구현 코드 검토
2. 실제 실행 환경에 AUTH_SECRET을 설정하고, 대상 DB 확인 후 마이그레이션 적용
3. 클라이언트의 초대 링크 연결 화면과 앱·웹 주소 설정

## Changed Files

- `pyproject.toml`, `uv.lock`: FastAPI Users 의존성 추가와 SQLAlchemy 2.0 호환 범위
- `app/models/`: DB 연결과 분리된 Base 및 모델 4개
- `app/core/database.py`: 기존 Base import 경로를 유지하면서 공통 모델 Base 재사용
- `app/repositories/`: 계정·가족·멤버·초대 DB 접근
- `app/schemas/`: API 요청 검증과 계정·가족·초대 응답
- `app/core/auth.py`, `app/core/exceptions.py`, `app/api/`: 인증 dependency와 회원·가족·초대 API
- `app/services/`: 가족 권한, 초대 정책, 쓰기 트랜잭션
- `app/core/config.py`, `app/main.py`: JWT·초대 설정과 오류 변환·로그
- `.env.example`, `compose.yaml`, `Dockerfile`, `README.md`: 실행 설정·마이그레이션 포함·실행 안내
- `alembic.ini`, `migrations/`: 초기 스키마 적용·롤백
- `tests/`: PostgreSQL 마이그레이션·제약·트랜잭션·잠금과 스키마·ORM 응답 변환 검증
- `docs/SPEC/SPEC-001.md`, `docs/PROGRESS.md`: 구현 상태와 후속 범위

## Commands Run

```text
uv sync --locked
잠금 파일 기준 동기화 성공

uv pip check
설치된 패키지 의존성 충돌 없음

uv run ruff check app migrations tests
통과

uv run ruff format --check app migrations tests
통과

uv run pytest -q
TEST_DATABASE_URL을 임시 PostgreSQL 18로 지정해 실행, 49 passed

docker build -t giantkiller-stage3-check .
Python 3.14 이미지 빌드 성공

이미지 내 alembic upgrade head
임시 PostgreSQL 18에 마이그레이션 적용 성공

이미지 내 앱 lifespan·가입·로그인·가족·초대 API 실행
성공

docker compose --env-file /dev/null config --quiet
테스트용 환경변수로 실행, Compose 설정 검증 성공
```

## Test Status

- Last passing: PostgreSQL 18 기준 전체 49개, Ruff, Docker 빌드·마이그레이션·앱 실행, Compose 설정
- Failing: 없음
- Not run: 기존 프로젝트 DB에 대한 마이그레이션 적용, 실제 앱·웹의 초대 링크 연결
- DB 테스트는 `TEST_DATABASE_URL`이 없으면 skip한다. 각 테스트는 고유 스키마를 만들고 제거한다.

## Risks / Open Questions

- AUTH_SECRET은 최소 32자로 새로 설정해야 한다. 기존 `.env`는 읽거나 수정하지 않았다.
- 초대 링크는 받은 계정을 미리 지정하지 않는 공유 링크다. 링크 소지자가 로그인 후 먼저 수락하면 참여한다.
- UserRepository의 변경 메서드는 FastAPI Users 어댑터 내부에서 commit한다. 가족 트랜잭션과 섞지 않는다.
- 일반 가입에서는 FastAPI Users의 safe 처리를 유지해 `is_superuser` 등 클라이언트 권한 입력을 적용하지 않는다.
- JWT 갱신·즉시 폐기는 지원하지 않으며 만료 후 재로그인한다.
- 앱·웹 초대 화면과 프록시 로그의 토큰 처리는 배포·클라이언트 작업이다.
- 테스트는 임시 PostgreSQL 컨테이너만 사용했으며 기존 프로젝트 DB·컨테이너에는 적용하지 않았다.
