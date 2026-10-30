# Progress: 회원·가족·초대, 가족 확인 및 알림 설계

Last updated: 2026-10-31 KST

## Goal

- FastAPI Users 계정과 가족·초대를 연결한다.
- 이번 브랜치의 DB 모델·레포지토리·마이그레이션, API 스키마, 인증·서비스·엔드포인트를 완료했다.
- PRD-003 가족 확인 서버 범위는 구현 1~3단계인 DB 기반·API·주기 만료 작업까지 완료했다.

## Current Status

- Status: PRD-004 독립 FID 관리·발송 서비스 구현 및 자동 테스트 완료, 실제 기기 연동 미검증
- Current focus: 독립 FCM 기능 검토. 가족 확인 호출 연결·실제 AI 연결·앱 설치 연관 분석은 후속
- Branch: `feature/push-notifications` (2026-10-31 전환 확인)
- Related issue/PR: 없음

## Decisions

- 2026-10-31: 사용자 지시에 따라 FCM 등록 토큰 대신 FID로 전환했다. API는 `/users/me/push-registrations`, 입력은 `fid`·`previous_fid`, DB는 `PushRegistration.fid`, 발송은 `Message(fid=...)`를 사용한다.
- 2026-10-31: PRD-004는 가족 확인 서비스와 연결하지 않고 FID 관리·내부 FCM 발송 기능을 독립적으로 설계한다.
- 2026-10-31: 사용자당 여러 앱 FID를 지원하고 모두 발송한다. 별도 기기 등록·기기 정보 관리 없이 로그인 사용자와 FCM에 등록된 FID만 연결한다.
- 2026-10-31: 서버의 제목·안내 문구와 사건 ID를 `notification + data`로 전달한다. 문구는 표시하고 사건 ID는 알림 선택 후 관련 화면 이동에 사용한다.
- 2026-10-30: 사용자가 PostgreSQL 유지를 확인했다. AGENTS.md의 SQLite 기술 스택은 적용하지 않는다.
- 2026-10-30: Python은 기존 프로젝트 설정인 3.14를 유지했다.
- 2026-10-30: FastAPI Users 어댑터와 호환되는 SQLAlchemy 2.0.55를 사용한다.
- 2026-10-30: 가족·멤버·초대 레포지토리는 DB 접근과 flush만 담당한다. 서비스에서 정책과 트랜잭션을 조합한다.
- 2026-10-30: PRD-001 구현에서는 FCM·기기 등록과 사칭 확인 응답 정책을 제외했다. 가족 확인은 후속 PRD-003에서 진행한다.
- 2026-10-30: 인증 기본값은 Bearer JWT·3600초다. 갱신·서버 측 폐기·로그아웃 API는 추가하지 않는다.
- 2026-10-30: 가족별 역할 겸용은 허용하고 계정 전체의 역할 제한은 두지 않는다.
- 2026-10-30: 초대 재취소는 204, 사용 완료는 409, 만료·취소 초대 수락은 410으로 처리한다.
- 2026-10-30: 미설정 초대 주소는 API 미리보기 URL로 반환하고, 앱·웹 링크 prefix는 환경변수로 설정한다.
- 2026-10-30: 사용자 지시에 따라 PRD-003은 자녀 사칭 확인·대응으로 한정한다. 앱 설치 연관 분석은 이번 완료 조건에서 제외하고 보류한다.

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
- [x] 최소 사건·확인 요청·자녀별 응답·알림 요청 모델과 저장소
- [x] `0002_verification` 마이그레이션과 DB 제약·집계·행 잠금·롤백 검증
- [x] 신규 저장소 테스트 26개, 임시 PostgreSQL 18에서 전체 75개 통과 및 Ruff 통과
- [x] 가족 확인 요청 생성·권한·응답 집계·10분 만료와 상태·알림 요청의 원자적 저장
- [x] 응답 POST·상태 조회 GET과 공통 스키마·오류 응답
- [x] 신규 스키마 17개·서비스 및 API 27개 테스트, 전체 119개와 Ruff 통과
- [x] 기동 직후·30초 간격 자동 만료와 종료 시 작업·DB 연결 정리
- [x] 신규 주기 작업·lifespan 테스트 8개, 임시 PostgreSQL 18에서 전체 127개 및 Ruff 통과
- [x] PRD-004 최소 FID 모델·저장소·마이그레이션과 인증된 등록·교체·연결 해제 API
- [x] 독립 FCM 발송, 고정 문구·ID·응답 액션, 부분 실패·무효 FID 정리·원문 없는 로그
- [x] 신규 테스트 22개, 새 PostgreSQL 18에서 전체 149개와 Ruff 검사·포맷 통과

## In Progress

- PRD-004 독립 서버 기능을 구현하고 `docs/SPEC/SPEC-004.md`에 결과를 반영했다. 실제 FCM·Android 수신 검증은 남아 있다.
- 가족 확인 서버 범위의 구현 1~3단계는 완료 상태를 유지한다.

## 후속 설계: PRD-004 알림 관리

- FID 등록·갱신 PUT과 연결 해제 DELETE, 최소 `PushRegistration` 모델, 내부 `PushService`를 구현했다.
- 기존 `NotificationRequest` 자동 처리와 가족 확인·사건 서비스 호출 연결은 하지 않는다.
- 큐·주기 발송·애플리케이션 자동 재시도·복구·SMS는 최소 구현에서 제외했다.
- FID 교체·계정 전환·로그아웃 연결 해제 실패, FCM 접수와 실제 수신의 차이를 명시했다.
- Android 표시 방식과 SDK 호환성, 실제 기기 발송은 연동 검증이 필요하다.
- AndroidManifest의 FID 등록 활성화와 `onRegistered(installationId)` 콜백으로 FCM에 등록된 FID를 업로드하는 계약을 README·SPEC에 명시했다. 기존 등록 토큰을 FID로 이름만 바꿔 사용하지 않는다.
- PR #3 검토 반영: Admin SDK 7.7.0의 동기 인증 I/O를 확인하고 초기화·인증·`send_each` 전체를 worker thread로 옮겼다. 초기화와 인증 포함 발송 대기 중 이벤트 루프 진행을 검사하는 회귀 테스트 2개를 추가했다.
- FID DB → API → 내부 발송·자동 검증을 완료했다. 기존 프로젝트 DB에는 적용하지 않았다.

## 후속 설계: PRD-003 가족 확인

- PRD-002보다 가족 확인 기능을 먼저 진행하기로 했다.
- `docs/SPEC/SPEC-003.md`에 설계와 구현 1~3단계 결과를 기록했다.
- 응답 대상은 모든 자녀다. 한 명이라도 `SENT`이면 발송 확인, 전원이 `NOT_SENT`이면 발송 부인으로 확정한다.
- 응답 기한은 요청 생성 후 10분이다. 일부 응답·무응답으로 기한이 지나면 확인 불가로 종료한다.
- 대기 중 부모에게 위험 행동 보류 안내를 요청한다. 발송 확인·발송 부인·만료를 안전 보증이나 사칭 확정으로 표현하지 않는다.
- 대상 자녀가 0명이면 즉시 `EXPIRED`로 종료하고 확인 불가로 안내한다. PRD-003과 SPEC-003에 반영했다.
- 해커톤에서는 최소 기능의 완전한 동작을 우선한다. 자녀 탈퇴·계정 비활성화 대응은 제외하고 기존 인증·가족 권한 검사를 재사용한다.
- 앱 설치와 통화 지시 연관 분석, 실제 AI·FCM·Android 연결은 후속 범위다.
- 설계 1단계: 응답 POST·상태 조회 GET과 내부 요청 생성 서비스로 정했다. 시연용 생성 API는 추가하지 않는다.
- 설계 2단계: 요청은 확인 요청 ID·응답 값, 공통 결과는 ID·사건 ID·상태·만료 시각·대상 수·응답 수·내 응답으로 정했다.
- 설계 3단계: SPEC-003에 `VerificationRequest`·`VerificationResponse` 두 모델의 필드·제약·집계·동시성 설계안을 작성했다. 대상 자녀는 미응답 상태부터 저장하고 집계 수는 조회로 계산한다.
- 설계 4단계 초안: 저장된 사건 ID로 확인 요청을 생성하고, 결과별 알림 요청을 DB에 함께 기록하는 연결 계약을 정리했다. 실제 FCM 발송은 후속이다.
- 사용자 승인에 따라 최소 `SecurityEvent` 모델을 이번 설계에 포함했다. 가족·부모·유형·분류·위험 수준·요약·상태·발생 및 저장 시각을 설계하고 확인 요청의 사건 외래 키를 연결했다.
- 사건 공개 API·분석 상태·통화 지시·완료 전이는 후속이다.
- 설계 5단계: 기존 오류 형식에 맞춰 401·403·404·409·410·422를 정리했다. 타 가족은 기존 정책과 같은 403이다.
- 만료는 lifespan의 asyncio 작업으로 기동 직후·30초 간격 처리하고 API에서도 검사하기로 했다. 응답 기한은 10분 그대로이며 별도 의존성을 추가하지 않는다.
- 결과별 부모·자녀 안내 문구 초안을 작성했다.
- SPEC-003에 영향 파일과 구현 계획을 기록했다. 구현 1단계는 DB·레포지토리·마이그레이션, 2단계는 스키마·서비스·API, 3단계는 주기 작업·앱 연결·전체 검증이다.
- 사용자 승인에 따라 구현 1단계 DB 기반, 2단계 스키마·서비스·API, 3단계 30초 주기 작업과 lifespan 연결을 완료했다.
- 사건 생성 재호출의 최신 상태 반환, 중복·동시 응답, 기한 정각·잠금 대기 중 만료, 알림 실패 롤백과 권한별 API 응답을 검증했다.
- 실제 lifespan에서 지난 요청 복구·반복 실행·DB 오류 후 재실행·행 잠금 대기 중 종료를 검증했다. API·주기 작업과 여러 만료 작업의 경합에서도 확정 결과와 알림이 중복되지 않는다.
- 기존 프로젝트 DB·컨테이너에는 적용하지 않았다. 새 테스트 전용 PostgreSQL 18에서 검증했다.

## Next Steps

1. SPEC-004 독립 FID 관리·FCM 발송 구현 검토
2. 실제 Firebase 테스트 기기 발송·Android 수신 검증
3. 가족 확인·AI 분석 호출 연결은 별도 후속. 앱 설치 연관 분석은 진행하지 않는다.

## Changed Files

- 2026-10-31 PRD-004: `app/models/push_registration.py`, `app/repositories/push_registration.py`, `app/schemas/push.py`, `app/services/push_registration.py`, `app/services/push.py`, `app/core/fcm.py`, `app/api/v1/push_registrations.py`, `migrations/versions/0003_push_registrations.py`, 모델 export·API dependency·router, 신규 테스트 2개 파일·기존 OpenAPI 테스트, README·PRD-004·SPEC-004·진행 문서. PRD에는 사용자 지시대로 FID 전환을 반영했다. 기존 DB는 변경하지 않았다.
- 이번 PRD-003 단계: `app/models/event.py`, `verification.py`, `notification.py`, `__init__.py`; `app/repositories/event.py`, `verification.py`, `notification.py`; `migrations/versions/0002_verification.py`; `tests/test_verification_storage.py`; 관련 PRD·SPEC·진행 문서
- PRD-003 구현 2단계: `app/schemas/verification.py`, `app/services/verification.py`, `app/api/v1/verify.py`, API dependency·router, 사건·가족·확인 저장소의 필요한 조회 보완, 신규 스키마·서비스 테스트, 기존 OpenAPI 테스트
- PRD-003 구현 3단계: `app/main.py`, `app/services/verification.py`, `tests/conftest.py`, `tests/test_verification_worker.py`, 관련 진행·요구사항·설계 문서
- 아래는 이전 PRD-001 구현의 파일 목록이다.
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

- Last passing: PR #3 발송 worker thread 수정 기준 임시 PostgreSQL 18 전체 151개와 Ruff 검사·포맷 통과
- 이번 실행: `TEST_DATABASE_URL`을 새 스레드 수정 테스트 컨테이너로 지정해 `uv --cache-dir /tmp/giantkiller-prd004-uv run --no-sync pytest -q --tb=short -W error::DeprecationWarning`, 151 passed in 19.53s, 경고 0개
- SDK 메시지 직렬화의 `fid` 키와 `token` 키 부재, 레거시 `token`·`previous_token` 요청 거부를 검증했다.
- 이전 PRD-001 검증: 전체 49개, Docker 빌드·마이그레이션·앱 실행, Compose 설정. 이번 DB 단계에서 이미지 빌드는 재실행하지 않았다.
- Failing: 없음
- Not run: 기존 프로젝트 DB에 대한 마이그레이션 적용, 실제 앱·웹의 초대 링크 연결
- DB 테스트는 `TEST_DATABASE_URL`이 없으면 skip한다. 각 테스트는 고유 스키마를 만들고 제거한다.
- PRD-003의 조회·응답 시 만료와 응답 정책·권한·API, API 호출 없는 자동 만료·실제 lifespan을 검증했다. 실제 AI·FCM·Android·앱 설치 연관 분석은 후속이다.

## Risks / Open Questions

- AUTH_SECRET은 최소 32자로 새로 설정해야 한다. 기존 `.env`는 읽거나 수정하지 않았다.
- 초대 링크는 받은 계정을 미리 지정하지 않는 공유 링크다. 링크 소지자가 로그인 후 먼저 수락하면 참여한다.
- UserRepository의 변경 메서드는 FastAPI Users 어댑터 내부에서 commit한다. 가족 트랜잭션과 섞지 않는다.
- 일반 가입에서는 FastAPI Users의 safe 처리를 유지해 `is_superuser` 등 클라이언트 권한 입력을 적용하지 않는다.
- JWT 갱신·즉시 폐기는 지원하지 않으며 만료 후 재로그인한다.
- 앱·웹 초대 화면과 프록시 로그의 토큰 처리는 배포·클라이언트 작업이다.
- 테스트는 임시 PostgreSQL 컨테이너만 사용했으며 기존 프로젝트 DB·컨테이너에는 적용하지 않았다.
