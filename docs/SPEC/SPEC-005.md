# SPEC-005: 통화 사건 저장·분석 결과·자녀 알림

- 관련 요구사항: [PRD-005](../PRD/PRD-005.md)
- 연결 계약: [PRD-002](../PRD/PRD-002.md), [SPEC-003](SPEC-003.md), [SPEC-004](SPEC-004.md)
- 작성일: 2026-10-31 KST
- 상태: 최소 내부 서비스 구현·자동 검증 완료. 자녀 조회 1차 구현은 11절에 기록한다. 실제 AI·Android 연결은 후속이다.

## 1. 이번 범위

사용자와 합의한 최소 흐름은 다음과 같다.

```text
입력·권한 검증을 마친 통화 접수
  → 사건 저장·commit (PENDING / OPEN)
  → 분석 시작 기록·commit (PROCESSING)
  → AI 결과 검증
      → 정상: 결과 저장·commit (COMPLETED / NOT_REQUIRED), 자녀 알림 없음
      → 위험·의심: 결과 저장·commit (COMPLETED / OPEN), 자녀 알림 시도
      → 호출·결과 검증 실패: 실패 저장·commit (FAILED / OPEN), 자녀 위험 알림 없음
```

- 통화 한 건을 사건 하나로 다루며, 정상 통화와 분석 실패도 사건을 유지한다.
- 이번에는 사건 DB·저장소, 내부 분석 상태·결과 저장 서비스, 기존 푸시 서비스 호출을 설계한다.
- 실제 AI 서버 없이도 내부 서비스를 테스트할 수 있어야 한다.
- PRD-005 전체가 아니라 통화 사건의 최소 저장·알림 범위다. 전체 수용 기준을 완료 처리하지 않는다.

이번 범위에서 제외한다.

- 당시 사건 목록·상세 API는 제외했으며, 이후 사용자 승인으로 11절의 최소 조회 범위를 추가했다.
- 자녀의 확인·대응 완료 변경 API. 대응 상태의 초기값·정상 결과 전이만 구현 대상이다.
- Android 업로드 API, 오디오 보관·STT·실제 AI HTTP 호출, 분석 worker와 작업 상태 조회 API. PRD-002 연결 작업으로 둔다.
- 알림·앱 설치 사건 생성, 통화 간 연관 분석·최근 발신자 맥락 조회
- 가족 확인 서비스 호출 연결과 기존 가족 확인 알림의 자동 발송
- 멱등성 키·재분석·자동 재시도·발송 큐·재시작 후 복구

## 2. 현재 코드에서 재사용할 부분

- `SecurityEvent`에 가족·부모 멤버·유형·분류·위험 수준·요약·대응 상태·시각이 있다.
- `classification`은 현재 필수다. 분석 전 사건을 저장하려면 미분류 상태를 허용해야 한다.
- `SecurityEventRepository`는 생성·ID 조회와 선택적 행 잠금을 지원한다. commit은 서비스 책임이다.
- `PushService.send(PushSend)`는 독립 세션으로 모든 등록 FID에 발송하고 부분 실패를 반환한다.
- `PushMessageType.CALL_SUMMARY`와 고정 문구·사건 ID 페이로드가 이미 있다.
- `NotificationRequest`는 `verification_id`가 필수이며 가족 확인 전용 유형만 지원한다. 이번 통화 알림을 위해 이 모델을 확장하지 않는다.
- 기존 DB는 PostgreSQL이며 새 의존성은 필요하지 않다.

## 3. DB 변경안

### 3.1. SecurityEvent 확장

기존 테이블·UUID·UTC 시각·대응 상태 필드 `status`를 유지한다.

```text
기존 필드
id, family_id, parent_member_id, occurred_at, created_at: 유지
event_type: 이번 신규 통화 사건은 CALL
classification VARCHAR(64): nullable로 변경, 분석 전·실패 시 NULL 허용
risk_level VARCHAR(32), summary TEXT: 기존 nullable 유지
status VARCHAR(32): OPEN / ACKNOWLEDGED / RESOLVED / NOT_REQUIRED

추가 필드
requires_attention BOOLEAN, nullable
  검증된 위험·의심 결과는 true, 정상 결과는 false
  분석 전·실패와 기존 사건은 NULL, 정상으로 간주하지 않음
analysis_status VARCHAR(32), nullable
  통화 사건: PENDING / PROCESSING / COMPLETED / FAILED
  기존 사건: NULL, 이번 기능에서 분석 상태를 추적하지 않았음을 뜻함
analysis_error_code VARCHAR(64), nullable
  실패 시 서버가 정한 고정 코드만 저장
```

- 새 통화는 `PENDING / OPEN`, 판정값·분류·위험 수준·요약·실패 코드는 NULL로 생성한다.
- 검증된 결과의 `requires_attention`을 사건에 함께 저장한다. 알림 대상 여부는 이 저장된 판정값을 사용하며, 대응 상태에서 역산하지 않는다.
- `COMPLETED`는 분석 성공이며 안전 보증이 아니다. 분석 결과와 구분한다.
- 정상 결과는 `NOT_REQUIRED`, 위험·의심 결과는 `OPEN`이다.
- `ACKNOWLEDGED`·`RESOLVED`의 사용자 변경은 후속이다. 가족 확인 결과로 자동 변경하지 않는다.
- 기존 사건의 분류·대응 상태를 보존하고, 근거 없이 분석 완료·실패를 소급 지정하지 않는다.
- 통화의 분석 상태는 서비스에서 필수로 설정한다. 분석 전 분류에 가짜 정상값을 넣지 않는다.

### 3.2. CallInstruction 신규 모델

```text
call_instructions
id                UUID PK
event_id          UUID FK → security_events.id, NOT NULL, INDEX
instruction_type  VARCHAR(64), NOT NULL
description       TEXT, nullable
```

- 사건 하나에 지시 0개 이상을 저장한다. 정상 결과도 검증된 지시가 있으면 저장할 수 있다.
- `description`은 구조화된 지시 설명이며 전체 통화 원문이 아니다.
- 지시 유형의 실제 값은 AI 계약에서 정한다. 임의의 송금·설치 유형 ENUM을 확정하지 않는다.
- 지시 존재 여부만으로 위험을 판정하지 않는다. 검증된 분석 결과의 위험·의심 여부를 사용한다.
- 이번에는 순서·이력·통화 간 연결 모델을 추가하지 않는다.

### 3.3. 마이그레이션

- 현재 head `0003_push_registrations` 다음에 `0004_call_events.py`를 추가한다.
- 분류 nullable 변경, 판정값·분석 필드 추가, 통화 지시 테이블 생성만 수행한다.
- 기존 사건·가족 확인·사용자·FID 데이터를 보존한다. `create_all()`로 대체하지 않는다.
- downgrade에서 `classification NOT NULL` 복원은 NULL 분류 사건이 있으면 불가능하다. 가짜 분류 삽입·사건 자동 삭제 대신 명확히 중단하고, 해당 데이터 처리 후 되돌리도록 안내한다.
- 기존 프로젝트 DB에는 이번 문서 작업으로 마이그레이션을 적용하지 않는다.

## 4. 내부 결과 계약 초안

`app/schemas/event.py`에 Pydantic 내부 입력 모델을 둔다. 공개 API나 AI 서버의 실제 응답 형식으로 간주하지 않는다.

```text
CallAnalysisResult
classification: str, 필수, 비어 있지 않음, 최대 64자
requires_attention: bool, 필수, 명시적인 정상 / 위험·의심 구분
risk_level: str | None, 최대 32자
summary: str | None
instructions: list[CallInstructionInput], 없으면 빈 목록

CallInstructionInput
instruction_type: str, 필수, 비어 있지 않음, 최대 64자
description: str | None
```

- `requires_attention`은 내부 정규화 필드 제안이다. 실제 AI가 같은 필드를 반환한다고 가정하지 않는다.
- PRD-002 어댑터가 AI의 허용된 분류값·위험 수준·지시 유형을 검증하고 내부 결과로 변환한다.
- 정상·위험·의심의 실제 분류 매핑은 AI 계약 연결 전에 확정해야 한다. 알 수 없는 분류·판정 누락을 `false`로 기본 처리하지 않는다.
- 알려지지 않은 필드, DB 문자열에 저장할 수 없는 NUL, 필수값 누락·길이 초과·잘못된 불리언을 거부한다. 원문을 오류 로그에 남기지 않는다.
- 결과 검증 실패는 분석 실패이며 안전 판정으로 대체하지 않는다.
- 실제 AI 계약이 없으므로 이 단계 검증은 명시적인 테스트 결과를 주입하는 내부 계약에 한정한다.

## 5. 내부 서비스와 트랜잭션

`EventService`는 요청/작업의 `AsyncSession`과 기존 `PushService`를 주입받는다. 저장소는 DB 조회·저장·flush만 수행한다.
이번에는 Router·공개 dependency·lifespan 작업을 추가하지 않는다. 향후 PRD-002 서비스가 호출한다.

### 5.1. 제안 메서드

```text
create_call(family_id, parent_user_id, occurred_at) → event_id
start_analysis(event_id)
complete_analysis(event_id, result)
fail_analysis(event_id, error_code)
```

- `create_call`은 지정 가족의 소속·PARENT 역할을 검사하고 서버가 부모 멤버 ID를 결정한다. 타 가족 멤버 ID를 그대로 신뢰하지 않는다.
- `parent_user_id`는 향후 호출자가 인증에서 얻은 값이다. 공개 클라이언트가 임의 지정하는 계약이 아니다.
- 현재 모델은 사건당 가족 하나다. 이번 내부 계약은 명시된 가족 하나를 처리하며, 부모의 여러 가족 중 선택·배포 방식은 PRD-002 실제 접수 연결 전에 확정한다. 모든 가족으로 자동 복제하지 않는다.
- `occurred_at`은 타임존 있는 입력을 검증해 UTC로 저장한다. 서버 저장 시각과 구분한다.
- 생성은 AI 호출 전에 commit한다. 분석 시작도 별도 트랜잭션으로 기록한다. 네트워크 호출 동안 DB 잠금·트랜잭션을 유지하지 않는다.
- 분석 완료는 사건 판정값·결과·상태와 모든 지시를 하나의 트랜잭션으로 저장한다. 일부 지시만 저장된 완료 상태를 허용하지 않는다.
- AI 호출·응답 검증 실패는 별도 트랜잭션으로 `FAILED`와 고정 실패 코드를 기록한다. 제안 코드는 `AI_REQUEST_FAILED`, `INVALID_ANALYSIS_RESULT`다.
- AI 호출·결과 검증을 수행하는 PRD-002 호출 서비스가 예외를 구분해 `fail_analysis()`를 호출할 책임을 갖는다. 검증 성공 시에만 `complete_analysis()`를 호출하며, 호출 실패·검증 실패 시에는 완료 처리를 호출하지 않는다. 새 worker나 별도 실패 처리 서비스를 추가하지 않는다.
- 이번 내부 서비스 검증에서는 테스트 호출부가 위 순서를 재현한다. 실제 AI 예외 포착·실패 기록의 전체 연결은 PRD-002 구현 시 검증한다.
- 저장소 오류는 트랜잭션을 롤백하고 호출자에게 전달한다. 분석 완료나 발송 성공으로 처리하지 않는다.

### 5.2. 전이 제한

```text
PENDING → PROCESSING → COMPLETED
                    → FAILED
```

- 상태 변경 시 기존 `get(..., for_update=True)`로 최신 사건을 읽는다.
- 통화가 아닌 사건·잘못된 순서의 호출은 거부한다. 완료·실패 사건의 재분석은 지원하지 않는다.
- 제안 오류는 없는 사건 `EVENT_NOT_FOUND`, 대상 유형 불일치 `EVENT_NOT_CALL`, 전이 불가 `EVENT_ANALYSIS_STATE_CONFLICT`다. 기존 `ServiceError` 형식을 사용한다.
- 같은 사건에 대한 중복 완료 호출을 거부하는 것은 내부 전이 검사다. 업로드 재전송의 중복 사건 방지를 보장하지 않는다.
- 프로세스가 종료되면 `PENDING`·`PROCESSING`이 남을 수 있다. 이번에는 자동 복구·재시도를 만들지 않는다.

## 6. 자녀 알림 연결

1. 검증된 결과의 `requires_attention`을 사건에 저장하고, 이 값이 true인 경우에만 사건 가족의 CHILD 멤버 사용자 ID를 조회한다.
2. 사건 결과·지시 저장을 commit한 뒤 DB 잠금을 해제한다.
3. 각 자녀에게 `PushSend(user_id, event_id, message_type=CALL_SUMMARY)`를 전달해 기존 `PushService.send()`를 한 번 호출한다.

- 수신자는 사건 가족의 모든 자녀다. 다른 가족·부모에게 이 경로로 발송하지 않는다.
- 정상 결과와 검증된 위험·의심 결과가 없는 대기·진행·실패 상태에서는 호출하지 않는다.
- FCM에는 기존 고정 문구와 사건 ID·메시지 유형만 보낸다. 요약·지시·전화번호·원본 녹음을 추가하지 않는다.
- 자녀가 없거나 활성 계정·등록 FID가 없으면 발송을 건너뛴다. 기존 `PushService`의 비활성 사용자·무효 FID 처리와 성공/실패 집계를 재사용한다.
- 발송 실패·부분 실패는 사건의 `COMPLETED`를 `FAILED`로 바꾸거나 저장된 분석 결과를 롤백하지 않는다. `FAILED`는 분석 실패다.
- 발송 호출 자체의 예외도 수신자별로 기록하고 다음 자녀 발송을 계속한다. 로그에는 사건·대상 ID, 고정 코드나 오류 타입만 남긴다.
- 새 알림 테이블·outbox·worker·자동 재시도는 만들지 않는다. commit 직후 프로세스 종료 시 알림이 누락될 수 있다.
- FCM 접수는 실제 기기 수신과 다르다. 실제 Firebase·Android 검증은 별도이며 본 설계만으로 전달을 보장하지 않는다.
- 현재 조회 화면·API는 후속이므로, 사건 ID를 받은 앱에서 상세를 여는 전체 흐름까지 이번 완료 범위로 주장하지 않는다.

## 7. 영향 파일과 구현 순서

### 1단계: DB·저장소

- `app/models/event.py`: nullable 판정값·분석 상태·실패 코드, 분석 전 nullable 분류, 대응 상태 값 정의
- `app/models/call_instruction.py` (신규): 사건에 연결된 지시 모델
- `app/models/__init__.py`: 신규 모델 export·메타데이터 등록
- `app/repositories/event.py`: 기존 생성 계약을 보존하면서 통화 생성·분석 상태·결과 저장 지원
- `app/repositories/call_instruction.py` (신규): 검증된 지시 일괄 저장
- `migrations/versions/0004_call_events.py` (신규): 기존 데이터 보존 마이그레이션
- `tests/test_event_storage.py` (신규): 기존 사건 보존, 분석 전 저장, 지시 FK·공동 롤백·마이그레이션 검증

### 2단계: 내부 서비스·알림 연결

- `app/schemas/event.py` (신규): 내부 결과·지시 입력 검증
- `app/services/event.py` (신규): 부모 소속·역할, 상태 전이·트랜잭션, 결과 저장 후 자녀 알림
- `tests/test_events.py` (신규): 정상·위험·분석 실패·권한·발송 실패 통합 검증
- `tests/test_event_schemas.py` (신규): 결과 계약의 필수값·잘못된 입력 검증
- `tests/test_verification_storage.py` 등 기존 테스트: 현재 head·메타데이터 검증과 기존 가족 확인 회귀 확인. 필요한 기대값만 조정한다.

기존 `PushService`·`PushSend`·가족 저장소·`transaction()`을 재사용한다. 이 설계에서는 수정할 이유가 없다.
실제 구현 중 변경이 필요하면 원인과 범위를 먼저 확인한다. `.env`, 의존성, 공개 Router와 조회 API는 수정 대상이 아니다.

### 3단계: 검증·진행 문서

- `docs/SPEC/SPEC-005.md`: 구현 결과와 실제 검증 범위 반영
- `docs/PROGRESS.md`: 단계별 완료·테스트·미검증 사항 갱신
- 사용자 구현 승인에 따라 아래 구현 결과까지 완료했다.

## 8. 검증 계획

- 독립된 PostgreSQL에서 접수 직후 사건의 `PENDING / OPEN`과 미분류 NULL 저장 확인
- 정상 결과는 사건을 유지하고 `requires_attention=false`, `COMPLETED / NOT_REQUIRED`, 자녀 알림 0회 확인
- 위험·의심 결과는 `requires_attention=true`와 결과·지시 저장 후 지정 가족의 자녀에게만 발송 시도 확인
- 다른 세션에서 완료 결과를 읽을 수 있는 시점에 푸시가 호출되는지 확인
- 테스트 호출부에서 결과 검증 실패 시 `fail_analysis()`를 호출해 `requires_attention=NULL`, `FAILED / OPEN`을 남기며 완료 처리·위험 알림을 호출하지 않는지 확인
- 지시 저장 실패는 완료 상태와 결과까지 함께 롤백하고 푸시를 호출하지 않는지 확인
- 부분 FCM 실패·발송 예외·자녀 없음·FID 없음에도 사건 보존 확인
- 잘못된 가족·부모 역할, 사건 유형·상태 전이 거부 확인
- 기존 가족 확인·알림·FID 데이터 보존, 기존 사건의 분석 상태 NULL과 가족 확인 동작 회귀 확인
- 마이그레이션과 ORM 메타데이터 일치, 안전하게 가능한 downgrade 및 NULL 분류 존재 시 중단 확인
- 요약·지시·원본·FID·AI 예외 원문이 로그/푸시로 유출되지 않는지 확인
- Ruff 검사·포맷 및 기존 전체 테스트 실행. 테스트 DB 미설정으로 skip된 실행을 통합 검증 통과로 보고하지 않는다.

이 검증은 내부 결과 주입과 가짜 FCM을 사용한다. 실제 업로드·AI·기기 수신·조회 화면은 검증하지 않는다.

## 9. 구현 전에 확인할 계약과 후속 TODO

내부 스키마·메서드·수신자 선정·`CALL_SUMMARY` 사용은 이번 설계 제안이며 실제 AI 계약의 확정값이 아니다.
실제 AI 연결 전에는 분류·위험 수준·지시 타입과 `requires_attention` 매핑, 접수 시 가족 선택을 확인해야 한다.

후속 TODO는 PRD-005의 목록을 유지한다.

- 통화 멱등성 키·중복 사건/알림 방지
- 재분석과 결과 변경 시 대응 상태·재알림 정책
- 자동 재시도·프로세스 종료 후 분석/발송 복구
- 최소 필드 외 추가 내용 공개와 원본 보존 정책. 대응 변경 API는 이후 12절에서 구현했다.


## 10. 구현 결과와 검증 (2026-10-31)

- `SecurityEvent`의 nullable 판정값·분류·분석 상태·고정 실패 코드, `CallInstruction`, `0004_call_events` 마이그레이션을 구현했다.
- `EventService`의 접수·시작·완료·실패, 부모 소속·역할 검사, 결과·지시 공동 저장을 구현했다. 대응 상태 결정은 서비스에서 수행한다.
- commit 후 사건 가족의 자녀에게만 기존 `PushService`를 호출한다. 발송 예외는 수신자별로 처리하며 완료된 분석 결과를 보존한다.
- 계획된 신규 테스트는 `tests/test_events.py` 한 파일에 모았다. 내부 입력·서비스·저장/롤백·다운그레이드 제한 검증 14개를 추가했다.
- 독립 PostgreSQL 18 컨테이너에서 전체 `168 passed in 20.28s`. `-W error::DeprecationWarning`으로 실행했으며 경고가 없다.
- Ruff 검사·포맷 검사와 `git diff --check`를 통과했다. 기존 테스트의 메타데이터·마이그레이션·가족 확인·FID 회귀 검증도 통과했다.
- `.env`와 기존 프로젝트 DB는 읽거나 변경하지 않았다. 마이그레이션은 테스트 DB에만 적용했다.
- 실제 업로드·AI API·FCM 기기 수신·조회는 검증 범위가 아니다. 자녀 알림 연결 테스트는 가짜 발송 서비스를 사용하고, 기존 FCM 테스트는 가짜 클라이언트를 사용한다.

## 11. 자녀 사건 조회 1차 구현 (2026-10-31)

사용자가 목록·상세 구현을 승인하고 공개 필드를 요약·분석 상태·대응 상태·시각으로 제한했다. 정상 통화는 공개하지 않는다. 이전 절의 조회 제외 범위보다 이 추가 계약을 우선한다.

### API·응답

- `GET /api/v1/events`: Bearer JWT, 필수 `family_id: UUID`, 선택 `limit: 1~100`(기본 20), `offset >= 0`(기본 0). `EventRead[]`를 반환하며 total 집계는 제공하지 않는다.
- `GET /api/v1/events/{event_id}`: Bearer JWT, `EventRead` 하나를 반환한다.
- 공통 응답: `id: UUID`, `summary: str | null`, `analysis_status: str | null`, `status: str`, `occurred_at: datetime`.
- 원문·녹음·전화번호·위험도·분류·통화 지시·가족 확인 정보는 반환하지 않는다. NULL 상태는 정상이나 완료로 보정하지 않는다.

### 권한·공개 조건

- 가족의 CHILD 멤버만 허용한다. 타 가족 403 `FAMILY_ACCESS_DENIED`, 부모 403 `CHILD_REQUIRED`, 미인증 401이다.
- 목록의 없는 가족은 404 `FAMILY_NOT_FOUND`, 없는 사건과 숨긴 사건의 상세는 404 `EVENT_NOT_FOUND`다. 잘못된 UUID·페이지 값은 422다.
- DB 조회 단계에서 공개 조건을 공통 적용한다. `CALL AND analysis_status=COMPLETED AND requires_attention=true` 또는 `ALERT AND classification=CHILD_IMPERSONATION`만 노출한다.
- 사칭 문자는 현재 PRD-002 저장 경로가 판정값·분석 상태를 채우지 않으므로 별도 조건으로 포함한다. 정상·미분석·실패 통화는 ID 직접 조회로도 공개하지 않는다.
- 목록은 `occurred_at DESC, created_at DESC, id DESC`로 정렬하고 공개 필터 뒤에 limit·offset을 적용한다.
- 대응 상태는 공개 필터로 사용하지 않으며 조회로 변경하지 않는다. 향후 정상 통화 공개 정책 변경은 저장소의 공통 공개 조건에서 처리할 수 있다.

### 영향 파일·검증

- `app/api/v1/events.py`, `app/services/event_query.py` 신규. 기존 `EventService`는 분석·발송 기능을 그대로 유지한다.
- `app/repositories/event.py`: 공통 공개 조건·목록·단건 조회, `app/schemas/event.py`: 최소 출력 모델 추가.
- API dependency·통합 router, OpenAPI 경로 검증, `tests/test_event_queries.py` 추가.
- 기존 미커밋 PRD-002 변경은 보존한다. 모델·마이그레이션·의존성·`.env`는 변경하지 않는다.
- 검증 대상: 자녀·부모·타 가족·미인증 권한, 정상·분석 대기·진행·실패 숨김, 기존 사칭 문자의 NULL 상태 유지, 최신순·동일 시각 정렬·페이지 처리, 목록·상세 출력 일치와 조회의 상태 변경 없음.

검증 결과: 독립 PostgreSQL 18에서 신규 9개 포함 전체 `190 passed in 31.18s`, DeprecationWarning 오류 처리. Ruff 검사·포맷과 diff 공백 검사를 통과했다. 실제 AI·Firebase·Android·배포 검증은 포함하지 않는다.

## 12. 자녀 대응 상태 변경 추가 구현 (2026-10-31)

사용자가 자녀의 확인·해결 선택 API 구현을 추가 승인했다. 이전 절의 대응 변경 제외 범위보다 이 절을 우선한다.

- `PATCH /api/v1/events/{event_id}/status`, Bearer JWT, JSON `{"status":"ACKNOWLEDGED"}` 또는 `{"status":"RESOLVED"}`. 성공 200, 응답은 기존 `EventRead` 5개 필드다.
- `EventStatusUpdate`는 허용된 두 상태만 받으며 추가 필드를 거부한다. 잘못된 상태·UUID·누락 입력은 422다.
- 기존 공개 필터를 재사용한다. 완료된 주의 필요 통화와 자녀 사칭 문자만 변경하며 정상·미분석·실패 사건과 없는 사건은 404 `EVENT_NOT_FOUND`다.
- 해당 가족의 CHILD만 허용한다. 미인증 401, 부모 403 `CHILD_REQUIRED`, 타 가족 403 `FAMILY_ACCESS_DENIED`다. 대표가 아닌 자녀도 허용한다.
- 전이: OPEN에서 ACKNOWLEDGED 또는 RESOLVED, ACKNOWLEDGED에서 RESOLVED. 동일 상태 재요청은 200이며 역행 등 불가 전이는 409 `EVENT_STATUS_CONFLICT`다. OPEN·NOT_REQUIRED로의 사용자 변경은 지원하지 않는다.
- `get_visible(..., for_update=True)`가 사건을 잠그고 최신 값을 다시 읽는다. 권한·상태 검사와 flush·commit은 하나의 서비스 트랜잭션에서 수행한다. 동시 해결·확인 요청에서도 RESOLVED를 ACKNOWLEDGED로 되돌리지 않는다.
- 분석·판정·발생 시각·가족 확인·알림은 변경하지 않는다. commit 이후 사건·사용자 ID와 상태 전이만 로그에 남긴다.
- `app/services/event_status.py` 신규, `app/schemas/event.py`, 사건 저장소·API·dependency 보완. 모델·DB 마이그레이션·의존성 변경은 없다.
- `tests/test_event_status.py`는 전이 6종, 권한·입력·숨김 및 DB 불변, 기존 사칭 문자의 NULL 분석 상태 보존, 동시 변경을 검증한다. OpenAPI 경로 기대값을 추가한다.

검증 결과: 독립 PostgreSQL 18에서 신규 8개 포함 전체 `198 passed in 42.01s`. Ruff 검사·포맷과 diff 공백 검사 통과. 기존 프로젝트 DB·실제 기기·배포에는 적용하지 않았다.

## 13. 자녀 조회 공개 필드 확대 (2026-10-31)

사용자 승인으로 PRD-005 6.0.2를 구현했다. 이전 절의 5개 필드 응답보다 이 절을 우선한다.

- `EventRead`: `id`, `event_type`, `classification`, `risk_level`, `summary`, `analysis_status`, `status`, `occurred_at` 8개 필드. NULL은 `null`로 반환한다. 목록과 PATCH 응답이 사용한다.
- `EventInstructionRead`: `instruction_type`만 가진다. `description`은 모델에 두지 않아 어떤 응답에도 나오지 않는다.
- `EventDetailRead(EventRead)`: `instructions: list[EventInstructionRead]`를 추가한다. 상세 응답만 사용하며 지시가 없으면 빈 목록이다.
- `CallInstructionRepository.list_by_event(event_id)`: `select()`로 사건의 지시를 `instruction_type, id` 순으로 조회한다. 생성 시각 컬럼이 없어 결정적 정렬을 위해 ID를 보조 키로 쓴다.
- `EventQueryService.get_detail`: 기존 순서(숨김·없는 사건 404, CHILD 권한 확인)를 유지한 뒤 지시를 조회한다. 목록은 지시를 조회하지 않는다. `EventStatusService`는 `EventRead`를 그대로 사용해 변경이 없다.
- 모델·마이그레이션·의존성·`.env`는 변경하지 않는다.

검증 결과: 독립 PostgreSQL 18에서 신규 1개 포함 전체 `207 passed`, DeprecationWarning 오류 처리. CALL 지시 2건의 유형 정렬과 설명 비노출, ALERT 빈 지시, NULL 분류·위험도, 목록의 `instructions` 부재, PATCH 8개 필드를 검증한다. Ruff 검사·포맷과 diff 공백 검사 통과.
