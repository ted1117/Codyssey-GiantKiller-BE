# SPEC-003: 자녀 사칭 확인·대응

- 관련 요구사항: [PRD-003](../PRD/PRD-003.md)
- 상태: 가족 확인 서버 범위 구현 1~3단계 완료, 검토 가능
- 구현 상태: DB 기반·스키마·서비스·응답 및 조회 API·30초 주기 만료 작업 완료

## 1. 목적과 이번 범위

PRD-002보다 먼저 자녀 사칭 의심 사건의 가족 확인 기능을 구현한다.
Android 앱과 AI 서버 없이 테스트 사건을 주입해 서버 동작을 검증한다.
아래의 제안은 확정된 제품 정책과 구분하며, 확인 후 구현한다.

이번 단계에 포함한다.

- 사칭 의심 사건에 대한 확인 요청 생성과 조회
- 로그인한 자녀의 발송 여부 응답
- 가족·응답 대상·기한 검증, 동시 응답과 만료 처리
- 확인 결과에 따른 알림 요청 생성과 테스트
- 가족 확인의 입력이 되는 최소 `SecurityEvent` 저장 모델, 사용자 승인 범위

후속 단계로 남긴다.

- PRD-002의 실제 AI 분석 결과 연결
- 최근 통화 지시와 앱 설치 이벤트 비교 및 `/actions/app-install`
- PRD-004의 기기·FCM 토큰 조회, 실제 푸시 발송
- Android의 수신 화면과 응답 버튼
- 자녀 탈퇴·계정 비활성화에 따른 대상 재선정과 진행 중 요청 보정

해커톤에서는 확인 요청 생성 → 자녀별 응답 집계 → 결과 확정·10분 만료가 완전히 동작하는 것을 우선한다.
계정 생명주기에 대한 별도 처리 없이 기존 로그인 인증과 가족·응답 대상 권한 검사를 재사용한다.

사용자 지시에 따라 PRD-003은 자녀 사칭 확인·대응으로 한정한다.
앱 설치 연관 분석은 현재 PRD의 완료 조건에서 제외하고 후속으로 보류한다.
자녀 사칭 확인 서버 범위는 구현 완료이며, 실제 AI·FCM·Android 연결 검증은 별도 연동 후속이다.

## 2. 기존 기반과 의존성

- 현재 회원 인증과 가족별 `PARENT`·`CHILD` 역할을 재사용한다.
- 대표 자녀 여부는 초대 관리 권한이다. 확인 응답 권한과 동일하다고 가정하지 않는다.
- 같은 사용자가 여러 가족에 속할 수 있으므로 요청에 연결된 가족 안에서 권한을 검사한다.
- 최소 사건 저장 모델과 알림 요청 저장을 구현했고 가족 확인 서비스에 연결했다.
- PostgreSQL, UUID, 타임존이 있는 UTC 시각을 사용한다.

## 3. 확정 정책과 남은 결정

### 3.1. 응답 대상 자녀

확정: 해당 가족의 모든 자녀에게 확인 요청을 보낸다.
한 명이라도 `SENT`이면 발송 확인, 대상 자녀 전원이 `NOT_SENT`이면 전원 발송 부인으로 확정한다.
일부 자녀만 `NOT_SENT`로 응답하면 대기를 유지한다.

요청 생성 시 가족의 자녀 목록을 응답 대상으로 저장하고, 이 목록을 기준으로 집계한다.
탈퇴·비활성화에 따른 대상 목록 변경이나 진행 중 요청 보정은 이번 범위에서 다루지 않는다.

실제 발신 자녀가 휴대전화 고장 등으로 응답할 수 없다면, 나머지 자녀의 발송 부인만으로 확정하지 않는다.
전원의 발송 부인도 등록된 계정들의 응답 결과이며 실제 발신자의 신원을 입증하지 않는다.

대상 자녀가 0명이면 생성 즉시 `EXPIRED`로 확정하고 확인 불가로 안내한다.
빈 대상 목록을 전원 `NOT_SENT`로 판정하지 않는다.

### 3.2. 만료

- 확정: 서버의 요청 생성 시각부터 10분 동안 응답을 허용한다. `expires_at = created_at + 10분`이다.
- 예외: 대상 자녀가 0명이면 기다리지 않고 즉시 `EXPIRED`로 종료하며 `expires_at = created_at`으로 저장한다.
- FCM 수신 시각이나 개별 응답 시각을 기준으로 기한을 연장하지 않는다.
- `expires_at <= 현재 시각`이면 만료이며, 잠금 획득 후 서버 시각으로 다시 검사한다.
- 확정: FastAPI lifespan에서 `asyncio` 작업을 시작해 기동 직후와 이후 30초 간격으로 만료 요청을 처리한다. 별도 스케줄러·큐 의존성은 추가하지 않는다.
- 조회·응답 시에도 만료를 검사한다. 응답 허용 기한은 정확히 10분이며 주기 작업의 실행 지연만큼 연장하지 않는다.
- 주기 작업은 API 요청과 분리된 DB 세션을 사용한다. 요청 행을 잠근 뒤 최신 상태를 검사해 응답 처리와 같은 규칙으로 확정한다.
- 정상 실행 중 무접근 요청의 DB 상태·알림 요청 기록에는 최대 약 30초와 처리 시간만큼 지연이 생길 수 있다. 실제 FCM 전달 시각을 보장하는 값은 아니다.
- 서버 중단 중에는 처리할 수 없다. 재기동 직후 지난 만료 요청을 처리하며 종료 시 작업 취소·종료를 기다린 뒤 DB 연결 풀을 닫는다.
- 주기 작업의 DB 오류는 해당 트랜잭션을 롤백하고 원문 없이 기록하며, 다음 주기에 다시 처리한다.
- 미확정 요청의 만료는 확인 불가이며, 사칭이나 안전으로 단정하지 않는다.
- 부모에게 기존에 알고 있던 연락처나 다른 가족을 통해 확인하도록 안내한다. 문구 초안은 4.4절을 따른다.

### 3.3. 상태와 응답

상태는 `PENDING`, `CONFIRMED`, `DENIED`, `EXPIRED`로 확정한다.
대상 자녀가 0명인 경우는 생성 즉시 `EXPIRED`이고, 그 외에는 아래 전이를 따른다.

| 현재 상태 | 조건 | 다음 상태 |
| --- | --- | --- |
| `PENDING` | 기한 내 최초 `SENT` | `CONFIRMED` |
| `PENDING` | 대상이 1명 이상이며 기한 내 전원 `NOT_SENT` | `DENIED` |
| `PENDING` | 미확정 상태로 만료 시각 도달 | `EXPIRED` |

- 확정 상태는 다른 응답이나 만료 처리로 덮어쓰지 않는다.
- 자녀별 응답을 저장한다. 한 자녀의 `NOT_SENT` 이후에도 다른 대상 자녀는 응답할 수 있다.
- 같은 자녀의 재전송은 같은 내용이어도 409로 거부한다. 만료된 요청은 410을 우선한다.
- `SENT`는 자녀의 발송 확인이며, 메시지나 링크 전체의 안전 보증이 아니다.
- 전원 `NOT_SENT`이면 부모 경고 요청을 생성하며, 안내는 '가족 전원이 발송을 부인했어요'로 표현한다. 사칭 확정이라고 표현하지 않는다.
- 대기 중에도 부모에게 송금·앱 설치·인증번호 전달 보류 안내를 즉시 요청한다. 실제 FCM 발송과 앱 표시는 후속 작업이다.

## 4. 사건·알림 연결 계약 초안

관련 사건 요구사항은 [PRD-005](../PRD/PRD-005.md)를 따른다.
사건 목록·상세 API와 통화 지시 모델 전체를 이번 가족 확인 범위로 가져오지는 않는다.

### 4.1. 사건에서 확인 기능으로

내부 호출 형태는 `VerificationService.create(event_id)`다.
호출자가 전달한 가족·부모 ID를 그대로 신뢰하지 않고, 저장된 사건에서 다음 정보를 읽는다.

```text
event_id             사건 UUID
family_id            사건의 가족 UUID
parent_member_id     사건 당사자인 부모 멤버 UUID
classification       CHILD_IMPERSONATION
```

- 사건을 조회하고 사칭 의심 분류인지 검사한 뒤 확인 요청을 생성한다.
- 부모 멤버의 가족·역할이 사건 정보와 일치하는지 검증한다.
- 클라이언트가 사건 ID와 가족 ID를 임의로 조합해 확인 요청을 생성하는 공개 API는 만들지 않는다.
- 사건당 확인 요청은 하나만 생성하며 중복 호출은 최신 상태의 기존 요청을 반환한다. 재호출로 기한이나 대상을 초기화하지 않는다.
- 생성 트랜잭션은 사건 행을 먼저 잠가 동일 사건의 동시 생성을 직렬화한다.
- 사건이 없거나 사칭 의심 분류가 아니면 생성하지 않는다.
- 사용자 승인에 따라 PRD-005의 최소 `SecurityEvent` 저장 모델을 이번 설계에 포함한다. 확인 요청의 `event_id`는 `security_events.id`를 참조한다.
- 사건 ID는 사건을 저장할 때 서버에서 생성한다. 테스트에서도 실제 사건 행을 먼저 저장한 뒤 내부 생성 서비스를 호출한다.
- 테스트 사건은 테스트에서만 주입한다. 검증되지 않은 사건 ID를 운영 환경에서 허용하는 것으로 대체하지 않는다.

### 4.2. 확인 기능에서 알림 기능으로

- 요청 생성 시 대상 자녀에게 보낼 확인 요청을 기록한다.
- 요청 생성 시 부모에게 보낼 위험 행동 보류 안내도 기록한다.
- `CONFIRMED` 확정 시 부모에게 보낼 발송 확인 결과를 기록한다. 안전 판정으로 표현하지 않는다.
- `DENIED` 확정 시 사건 당사자 부모에게 보낼 경고 요청을 기록한다.
- 미확정 요청 만료 시 부모에게 보낼 확인 불가 안내를 기록한다.
- 최소 전달 정보는 사건 ID, 확인 요청 ID, 대상 사용자 ID, 메시지 유형이다.
- 메시지 유형은 `VERIFY_REQUEST`, `VERIFY_WAITING`, `VERIFY_CONFIRMED`, `VERIFY_DENIED`, `VERIFY_EXPIRED`다.
- 대상 자녀가 0명이면 자녀 확인 요청과 대기 안내를 만들지 않고 부모의 확인 불가 안내만 기록한다.
- 민감한 메시지·통화 원문은 알림 페이로드와 로그에 포함하지 않는다.
- 상태 변경과 알림 요청 저장은 같은 트랜잭션으로 확정한다.
- 최소 저장 모델 제안은 `NotificationRequest`다. 필드는 UUID PK, 사건 ID, 확인 요청 ID FK, 대상 사용자 ID FK, 메시지 유형, 생성 시각이다.
- 동일 요청·대상 사용자·메시지 유형에 UNIQUE 제약을 두어 중복 생성하지 않는다.
- 이 행은 발송 의도를 저장한다. 큐·재시도·발송 상태·FCM 클라이언트는 PRD-004에서 추가한다.
- PRD-004가 미구현인 동안 실제 전달 완료로 표시하지 않는다.
- 이번 단계는 저장된 알림 요청을 검증하며 실제 전달 완료로 취급하지 않는다.

### 4.3. 최소 SecurityEvent 설계안

테이블 이름은 `security_events`로 제안한다.
이번 단계에서는 사칭 의심으로 분류된 알림 사건을 저장해 가족 확인에 연결하는 데 사용한다.

```text
id                  UUID PK
family_id           UUID FK → families.id, NOT NULL
parent_member_id    UUID FK → family_members.id, NOT NULL
event_type          VARCHAR(32), NOT NULL, 이번 단계 입력은 ALERT
classification      VARCHAR(64), NOT NULL, 확인 요청 대상은 CHILD_IMPERSONATION
risk_level          VARCHAR(32), nullable
summary             TEXT, nullable
status              VARCHAR(32), NOT NULL, 초기값 OPEN
occurred_at         UTC datetime, NOT NULL
created_at          UTC datetime, NOT NULL
```

- `event_type`은 알림·통화 등 입력 채널, `classification`은 AI의 위험 분류다. 서로 다른 정보로 저장한다.
- 분류·위험 수준의 전체 값 목록은 PRD-002의 AI 계약에서 확정한다. 지금 임의의 전체 ENUM을 만들지 않는다.
- `risk_level`과 `summary`는 확인 처리에 필수인 정보가 아니므로 미제공 시 `null`을 허용한다. 미제공 위험 수준을 안전으로 대체하지 않는다.
- `OPEN`은 사건의 초기 관리 상태이며 확인 요청 상태와 별개다. 자녀가 `SENT`라고 답해도 사건을 안전·완료로 바꾸지 않는다.
- `occurred_at`은 사건 발생 시각, `created_at`은 서버 저장 시각이다. 확인 요청의 10분은 확인 요청 자체의 `created_at`부터 계산한다.
- 부모의 가족 소속과 `PARENT` 역할은 확인 요청 생성 서비스에서 검사한다. 확인 요청의 가족·부모 정보는 사건에서 복사하고 클라이언트 입력으로 덮어쓰지 않는다. 사건 수집·저장 서비스 자체는 후속 범위다.
- 메시지·음성 원문은 저장하지 않는다. 요약은 알림 페이로드나 로그로 자동 복사하지 않는다.
- 이번에는 사건 저장·ID 조회 레포지토리만 제공한다. 사건 생성·목록·상세 공개 API, 분석 작업 상태, 통화 지시, 사건 완료 전이는 후속이다.
- 사건 수집 단계의 중복 식별 키는 PRD-002·005에서 정한다. 여기서는 동일한 저장 사건 ID에 확인 요청 하나만 만드는 것을 보장한다.

이 부분 구현만으로 PRD-005 전체 또는 실제 AI 분석 연결이 완료되는 것은 아니다.

### 4.4. 안내 문구 초안

- `VERIFY_REQUEST`, 자녀: '가족에게 온 문자를 직접 보냈나요? 10분 안에 확인해 주세요.'
- `VERIFY_WAITING`, 부모: '가족에게 확인 중이에요. 확인 전에는 송금·앱 설치·인증번호 전달을 멈춰 주세요.'
- `VERIFY_CONFIRMED`, 부모: '자녀가 직접 보냈다고 답했어요. 메시지나 링크의 안전을 보장하는 것은 아니에요.'
- `VERIFY_DENIED`, 부모: '가족 전원이 발송을 부인했어요. 송금·앱 설치·인증번호 전달을 멈추고 다른 연락 수단으로 확인하세요.'
- `VERIFY_EXPIRED`, 부모: '가족 확인이 완료되지 않았어요. 기존에 알고 있던 연락처나 다른 가족을 통해 확인하세요. 확인 전에는 송금·앱 설치·인증번호 전달을 멈춰 주세요.'

자녀 확인 문구는 확인 요청 생성 시 기준이며 실제 수신 시 남은 시간이 10분이라는 보장은 아니다.
앱에서 남은 시간을 표시할 때는 서버의 `expires_at`을 기준으로 한다.

## 5. API 계약

공통 prefix는 `/api/v1`이며 기존 Bearer JWT 인증을 사용한다.

### 5.0. 1단계: 엔드포인트

이번 가족 확인 단계의 공개 API는 아래 두 개다.

| 메서드·경로 | 용도 | 권한 |
| --- | --- | --- |
| `POST /api/v1/verify/respond` | 로그인한 자녀의 발송 여부 응답 | 해당 요청의 대상 자녀 |
| `GET /api/v1/verify/{verification_id}` | 대기·확정·만료 상태 조회 | 해당 가족 멤버 |

- 확인 요청 생성은 공개 API 대신 사건 처리에서 호출할 내부 서비스로 둔다.
- PRD-002 연결 전에는 테스트에서 사건 정보를 준비해 내부 생성 서비스를 호출한다.
- 시연은 영상으로 진행하므로 별도의 시연용 생성 API는 추가하지 않는다.
- 만료는 서버가 처리하므로 클라이언트용 만료 API를 추가하지 않는다.
- 앱 설치 이벤트 API는 이번 단계에서 제외한다.
- 설계한 요청·응답 스키마를 구현 2단계에서 실제 API에 연결했다.

### 5.1. 응답

PRD에 지정된 `POST /verify/respond`를 사용한다.

```text
요청
  verification_id: UUID
  response: SENT / NOT_SENT

성공 응답: 200, 조회와 공통 결과 스키마 사용
  id: UUID
  event_id: UUID
  status: PENDING / CONFIRMED / DENIED / EXPIRED
  expires_at: UTC datetime
  target_count: int >= 0
  responded_count: int >= 0
  my_response: SENT / NOT_SENT / null
```

사용자·가족·응답 시각은 서버가 결정한다. 요청에 추가 필드는 허용하지 않는다.
`target_count`는 생성 당시 대상 자녀 수이며, `responded_count`는 실제 응답한 자녀 수다.
`my_response`는 현재 로그인한 사용자의 응답이며, 미응답 또는 비대상이면 `null`이다.
한 명의 `SENT`로 확정되면 `responded_count < target_count`여도 정상이다.
응답 API에서 `EXPIRED` 요청에 응답하려는 시도는 성공 응답 대신 410으로 거부한다.

오류 응답은 기존 `ServiceError`와 `{"detail": "오류 코드"}` 형식을 재사용한다.
인증과 입력 검증은 기존 FastAPI Users·FastAPI 형식을 유지한다.

- 401: 인증 실패, 기존 인증 응답 유지
- 403 `FAMILY_ACCESS_DENIED`: 해당 가족 멤버가 아님
- 403 `VERIFICATION_RESPONDER_REQUIRED`: 가족 멤버지만 응답 대상 자녀가 아님
- 404 `VERIFICATION_NOT_FOUND`: 확인 요청 없음
- 409 `VERIFICATION_FINALIZED`: 발송 확인·전원 발송 부인으로 이미 확정
- 409 `VERIFICATION_ALREADY_RESPONDED`: 대기 중이지만 해당 자녀가 이미 응답
- 410 `VERIFICATION_EXPIRED`: 만료된 요청에 응답 시도
- 422: 잘못된 UUID·응답 값·추가 필드, 기존 입력 검증 응답 유지

타 가족 접근은 기존 가족 API와 동일하게 403을 사용한다.
서비스 검사는 요청 존재 → 가족 소속·응답 대상 권한 → 상태·만료 → 개인 중복 응답 순서다.
이미 `CONFIRMED`·`DENIED`인 요청은 시간이 지나도 409이며 `EXPIRED`로 바꾸지 않는다.
`PENDING`에서 기한이 지났으면 만료 상태·알림 요청을 commit한 뒤 410을 반환한다.
권한 없는 호출은 상태를 변경하거나 처리 상태를 노출하지 않는다.

내부 요청 생성 실패는 별도로 구분한다. 사건이 없으면 `EVENT_NOT_FOUND`, 가족·부모 정보가 잘못되면 `EVENT_PARENT_MISMATCH`, 대상 분류가 아니면 `EVENT_NOT_VERIFIABLE`로 기록하며 확인 요청을 생성하지 않는다.
이 내부 실패를 위한 공개 API는 추가하지 않는다.

### 5.2. 조회

`GET /verify/{verification_id}`로 확인 상태를 조회한다.
조회는 앱 없는 API 검증과 향후 화면 연결에 사용한다.
200 응답은 5.1절과 동일한 공통 결과 스키마를 사용하며, 만료 상태도 조회할 수 있다.
기존 인증을 통과한 해당 가족 멤버만 조회할 수 있다.

## 6. 3단계: DB 모델 설계안

확인 요청과 대상 자녀별 응답을 두 테이블로 분리한다.
기존 모델과 동일하게 UUID와 타임존이 있는 UTC 시각을 사용한다.

### 6.1. VerificationRequest

테이블 이름 제안은 `verification_requests`다.

```text
id                  UUID PK
event_id            UUID FK → security_events.id, NOT NULL, UNIQUE
family_id           UUID FK → families.id, NOT NULL
parent_member_id    UUID FK → family_members.id, NOT NULL
status              ENUM: PENDING / CONFIRMED / DENIED / EXPIRED, NOT NULL
created_at          UTC datetime, NOT NULL
expires_at          UTC datetime, NOT NULL
finalized_at        UTC datetime, nullable
```

- `parent_member_id`는 사건 당사자 부모다. 해당 가족의 `PARENT`인지 서비스에서 검사한다.
- 사건당 요청 하나를 유지하도록 `event_id`에 유일성 제약을 둔다.
- `event_id`는 최소 사건 모델을 참조한다. 서비스에서 사건을 조회해 분류·가족·부모를 검사하고 DB 외래 키로 존재를 보장한다.
- 대상이 있으면 `PENDING`, 만료 시각은 생성 후 10분이다.
- 대상이 0명이면 생성부터 `EXPIRED`이며 생성·만료·확정 시각을 동일하게 저장한다.
- `finalized_at`은 실제 상태 확정 처리 시각이며, 만료 시각과 구분한다.
- CHECK: `expires_at >= created_at`, `finalized_at IS NULL OR finalized_at >= created_at`.
- CHECK: `PENDING`이면 `finalized_at IS NULL`, 나머지 상태이면 `finalized_at IS NOT NULL`.
- 미확정 요청의 만료 조회를 위해 `status`, `expires_at`의 복합 인덱스를 둔다.

### 6.2. VerificationResponse

테이블 이름 제안은 `verification_responses`다.

```text
id                  UUID PK
verification_id     UUID FK → verification_requests.id, NOT NULL
member_id           UUID FK → family_members.id, NOT NULL
response            ENUM: SENT / NOT_SENT, nullable
responded_at        UTC datetime, nullable
```

- 요청 생성 시 대상 자녀마다 한 행을 만들고 `response`, `responded_at`은 `null`로 둔다. 따라서 미응답 대상도 저장된다.
- `UNIQUE(verification_id, member_id)`로 한 요청에 동일 자녀가 중복 등록되지 않게 한다.
- CHECK: `response`와 `responded_at`은 모두 `null`이거나 모두 값이 있어야 한다.
- 응답 시 기존 대상 행을 한 번만 갱신한다. 이미 응답한 행의 재응답은 서비스에서 거부한다.
- 자녀가 요청의 가족에 속한 `CHILD`인지 대상 생성 시 서비스에서 검사한다.

### 6.3. 응답 집계

- `target_count`: 해당 요청의 자녀별 행 수.
- `responded_count`: `response IS NOT NULL`인 행 수.
- `my_response`: 로그인 사용자의 멤버에 해당하는 행의 응답, 미응답·비대상이면 `null`.
- 집계 수를 요청 테이블에 중복 저장하지 않고 자녀별 행에서 계산한다.
- `SENT`가 하나라도 있으면 `CONFIRMED`다.
- 대상 수가 1 이상이고 전원이 `NOT_SENT`이면 `DENIED`다.
- 그 외에는 기한 내 `PENDING`, 기한이 지나면 `EXPIRED`다. 이미 확정된 상태는 변경하지 않는다.

### 6.4. 트랜잭션과 동시성

- 요청과 대상 자녀별 행 생성은 하나의 트랜잭션으로 처리한다.
- 주기 만료의 한 실행은 대상 조회·요청별 잠금·상태 변경·알림 요청 저장을 하나의 트랜잭션으로 묶는다. 중간 오류나 취소가 발생하면 그 실행의 미확정 변경을 모두 롤백한다.
- 응답 처리는 확인 요청 행을 잠근 뒤 최신 상태·권한·만료 시각을 검사한다.
- 만료 작업도 동일한 행 잠금과 판정 규칙을 사용한다.
- 요청 행 잠금 안에서 자녀별 응답을 저장하고 전체 응답을 집계한다. `SENT` 하나 또는 전원 `NOT_SENT`일 때만 확정한다.
- 잠금 대기 중 기한이 지난 요청은 거부한다. 일부 `NOT_SENT` 응답만으로 확정하지 않는다.
- 만료 상태를 저장한 뒤 410을 반환할 때 예외 처리로 해당 저장까지 롤백되지 않도록 트랜잭션을 구성한다.
- Router는 검증·호출·응답, Service는 정책·트랜잭션, Repository는 조회·저장·잠금을 담당한다.

## 7. 검증 계획

- 기존 가입·가족·초대 흐름으로 부모·자녀 계정 준비
- 테스트 사건으로 요청 생성, 사건별 중복 생성과 잘못된 부모·가족 조합 거부
- 실제 `SecurityEvent` 행 저장 후 확인 요청 생성, 없는 사건 ID·대상 외 분류 거부, 사건 가족·부모 정보 일치 검증
- 위험 수준·요약 미제공 허용과 `SENT` 응답 후 사건의 `OPEN` 상태 유지
- 한 명의 `SENT`로 발송 확인, 전원 `NOT_SENT`로 발송 부인 확정과 부모 경고 요청 저장
- 일부 `NOT_SENT` 후 대기 유지, 나머지 무응답 시 확인 불가로 종료
- 자녀 1명인 가족은 해당 자녀의 `NOT_SENT`로 전원 발송 부인 조건 충족
- 대상 자녀 0명은 생성 즉시 `EXPIRED`, 집계 수 0, 확인 불가 안내이며 `DENIED`로 판정하지 않음
- 응답·조회 공통 스키마와 현재 사용자 기준 `my_response`, 한 명의 `SENT` 이후 응답 수가 대상 수보다 작은 경우
- 부모의 위험 행동 보류 안내 요청과 만료 안내 요청 저장
- 부모·타 가족·비대상 자녀의 응답 거부, 기존 로그인 인증 적용
- 생성 후 10분의 직전·정각·직후, 무응답 요청의 주기적 만료 처리
- 30초 주기 작업과 조회·응답의 만료 판정 일치, 만료 상태 저장 후 410 반환 시 저장 유지
- 앱 재기동 후 지난 요청 처리, 주기 작업의 오류 후 다음 실행과 종료 시 작업 정리
- 타 가족·비대상의 403, 중복·확정의 409, 만료 응답의 410과 만료 조회의 200
- 중복 응답, 서로 다른 자녀의 동시 응답, 응답과 만료 작업의 경합
- 알림 요청 저장 실패 시 상태 변경까지 롤백되는지 확인
- 로그·응답·알림 페이로드에 민감한 원문이 포함되지 않는지 확인

실제 AI 판정, FCM 전달, Android 화면·버튼은 이번 검증으로 입증하지 않는다.

## 8. 영향 파일과 단계별 구현 계획

설계 진행과 코드 구현을 구분한다. 아래 계획의 구현 1~3단계를 완료했다.
각 구현 단계가 완료되면 검증 결과를 이 문서와 `docs/PROGRESS.md`에 기록한다.
기존 회원·가족·초대 기능은 재사용하고 별도 리팩토링이나 의존성 추가는 하지 않는다.

### 구현 1단계: DB 모델·레포지토리·마이그레이션

신규 파일:

- `app/models/event.py`: 최소 `SecurityEvent`와 사건 저장 필드
- `app/models/verification.py`: 확인 요청·대상 자녀별 응답과 상태 ENUM
- `app/models/notification.py`: FCM 연결 전 알림 요청 저장과 메시지 유형
- `app/repositories/event.py`: 사건 생성·ID 조회
- `app/repositories/verification.py`: 요청·대상 생성, 조회·행 잠금·집계·응답·만료 갱신
- `app/repositories/notification.py`: 알림 요청 저장
- `migrations/versions/0002_verification.py`: 위 네 테이블의 생성·제약·인덱스와 역순 롤백
- `tests/test_verification_storage.py`: DB 제약·외래 키·마이그레이션·레포지토리 검증

수정 파일:

- `app/models/__init__.py`: 모델 등록과 Alembic 메타데이터 연결

레포지토리는 flush만 수행하고 commit하지 않는다.
이 단계에서는 응답 API나 주기 작업을 연결하지 않는다.
분리된 테스트 PostgreSQL에서 마이그레이션 왕복·메타데이터 일치와 기존 테스트를 확인한다.

### 구현 2단계: 스키마·서비스·API

신규 파일:

- `app/schemas/verification.py`: 응답 입력과 공통 결과 스키마
- `app/services/verification.py`: 내부 생성, 권한 검사, 응답 집계, 상태 조회·만료, 상태와 알림 요청의 원자적 저장
- `app/api/v1/verify.py`: 응답 POST와 상태 조회 GET
- `tests/test_verification_schemas.py`: UUID·응답 값·추가 필드·결과 스키마 검증
- `tests/test_verification.py`: 생성·집계·권한·10분 경계·0명·동시 응답·만료·알림 저장 실패 롤백 검증

수정 파일:

- `app/api/dependencies.py`: `Annotated` 서비스 dependency
- `app/api/router.py`: `/api/v1`에 verify 라우터 등록
- `app/repositories/event.py`: 생성 직렬화를 위한 사건 행 잠금 조회
- `app/repositories/family.py`: 사건 부모 멤버 ID 조회
- `app/repositories/verification.py`: 동일 세션의 재호출도 최신 요청 상태로 조회
- `tests/test_api.py`: OpenAPI 예상 경로에 합의한 verify API 두 개 추가

실제 사건 행을 저장한 뒤 내부 서비스를 호출해 요청을 준비하고, 로그인 사용자로 실제 API를 호출한다.
`NOT_SENT` 일부 응답 후 `PENDING` 유지, 한 명의 `SENT`, 전원 `NOT_SENT`, 0명, 만료 후 410을 검증한다.
만료 상태·알림 요청은 410 응답 이후에도 DB에 남아 있어야 한다.

### 구현 3단계: 주기 작업·앱 연결·전체 검증

수정 파일:

- `app/main.py`: 기동 직후·30초 간격 만료 작업 시작과 종료 시 취소·정리
- `app/services/verification.py`: 기한이 지난 요청을 주기 작업에서 잠금·확정하는 호출 연결
- `tests/conftest.py`: lifespan 검증도 테스트 DB 세션 팩토리를 사용하도록 연결

신규 파일:

- `tests/test_verification_worker.py`: 기동 처리·반복 실행·DB 오류 후 계속 실행·종료·API와의 경합 검증

검증에서는 실제 10분·30초를 기다리는 대신 시각·실행 대기를 제어한다.
기존 회원·가족·초대 테스트를 포함한 전체 테스트와 Ruff 검사를 실행한다.
실제 lifespan에서 별도 테스트 DB만 사용하는지 확인해 기존 프로젝트 DB에 접근하지 않는다.
기존 프로젝트 DB 마이그레이션 적용, 배포, 실제 AI·FCM 연결은 이 계획에 포함하지 않는다.

### 공통 문서 갱신

- `docs/SPEC/SPEC-003.md`: 구현한 모델·동작과 검증 결과 반영
- `docs/PROGRESS.md`: 단계별 진행·테스트 결과·남은 범위 반영

## 9. 구현 1단계 결과

- `SecurityEvent`, `VerificationRequest`, `VerificationResponse`, `NotificationRequest`와 ENUM 3개를 구현했다.
- 사건·확인·알림 레포지토리는 flush만 수행하며 commit·rollback은 호출자가 담당한다.
- 확인 저장소는 대상 등록, 요청 조회·행 잠금, 개인 응답 조회, 대상·응답·발송 확인 수 집계, 상태·응답 기록, 만료 대상 ID 조회를 제공한다.
- `0002_verification` 마이그레이션은 기존 회원 스키마 위에 네 테이블과 제약·인덱스를 추가한다.
- 신규 저장소 테스트 26개를 추가해 기존 테스트 포함 전체 75개가 임시 PostgreSQL 18에서 통과했다.
- ORM과 마이그레이션 메타데이터 일치, 전체 및 이번 단계만의 downgrade·upgrade 왕복, 기존 계정 보존과 신규 ENUM 제거를 확인했다.
- 중복·외래 키·상태 및 시각 CHECK·ENUM 제약, 공동 롤백, 응답 및 상태 변경 후 알림 저장 실패 롤백, 만료 정각 조회, 행 잠금·재조회 갱신을 검증했다.
- `uv run --no-sync ruff check app migrations tests`, `uv run --no-sync ruff format --check app migrations tests`를 통과했다.
- 기존 프로젝트 DB에는 마이그레이션을 적용하지 않았다. 검증에는 새로 생성한 테스트 전용 PostgreSQL만 사용했다.
- 1단계 당시 가족·부모·분류 검증, 중복 응답 거부, 전원 응답 판정, 10분 계산, 알림 생성 정책과 API는 미구현이었다. 해당 기능은 아래 2단계에서 서비스에 연결했다.

## 10. 구현 2단계 결과

- `VerificationRespond`, `VerificationRead` 스키마와 `POST /api/v1/verify/respond`, `GET /api/v1/verify/{verification_id}`를 연결했다.
- 공개 생성 API 없이 `VerificationService.create(event_id)`로 실제 사건을 조회·검증하고 자녀 대상과 부모·자녀 알림 요청을 함께 생성한다.
- 사건 행 잠금과 요청별 유일성 제약으로 동시 생성에서도 요청·대상·알림이 중복되지 않으며, 재호출은 기한을 연장하지 않고 최신 상태를 반환한다.
- 가족·대상 자녀 권한을 검사하고 개인 응답은 한 번만 기록한다. 한 명의 `SENT` 또는 전원 `NOT_SENT`로만 확정한다.
- 자녀 0명은 생성 즉시 `EXPIRED`와 부모 확인 불가 알림 요청 하나만 저장한다.
- 응답·조회는 요청 행 잠금 후 시각을 검사한다. 기한 정각과 잠금 대기 중 기한 경과는 응답을 거부한다.
- 기한이 지난 응답의 만료 상태·알림은 먼저 commit하고 이후 410을 반환한다. 만료 조회는 200으로 현재 상태를 반환한다.
- 이미 확정된 요청은 기한이 지나도 결과를 덮어쓰지 않는다. 사칭 사건 자체의 `OPEN` 상태는 발송 확인으로 안전·완료 처리하지 않는다.
- 알림 저장 실패 시 생성·대상·응답·확정의 부분 쓰기를 롤백하며, 로그는 고정 코드·ID·상태만 기록한다.
- 스키마 테스트 17개와 서비스·API 테스트 27개를 추가했다. 임시 PostgreSQL 18에서 기존 테스트 포함 전체 119개가 통과했다(`119 passed in 16.99s`).
- 같은 자녀·여러 자녀의 동시 응답, 동시 생성, 조회와 응답의 동시 만료, 잠금 대기 중 기한 경과, 오류 코드, 권한 없는 호출의 상태 보존을 검증했다.
- Ruff 검사·포맷 검사와 `git diff --check`를 통과했다.
- 2단계 당시 만료는 권한 있는 조회·응답 호출 때만 처리했다. API 호출 없는 자동 만료와 lifespan 연결은 아래 3단계에서 완료했다.
- 검증에는 새 테스트 전용 PostgreSQL만 사용했다. 기존 프로젝트 DB에는 마이그레이션을 적용하지 않았으며 실제 AI·FCM·Android 연결은 검증하지 않았다.

## 11. 구현 3단계 결과

- `VerificationService.expire_due()`가 기한이 지난 대기 요청을 조회하고, 각 요청의 행 잠금 후 최신 상태·시각을 다시 검사한다.
- 기한 내 응답이 먼저 확정된 요청은 잠금 대기 후에도 덮어쓰지 않으며, 여러 주기 작업·API가 겹쳐도 만료 알림 요청은 한 번만 생성한다.
- FastAPI lifespan에서 만료 작업을 시작한다. 기동 직후 한 번 실행하고, 각 실행 후 30초 대기한 다음 별도 DB 세션으로 반복한다.
- 오류 시 트랜잭션과 세션을 정리하고 다음 주기에 다시 처리한다. 로그에는 고정 메시지와 오류 타입만 남기며 SQL·예외 원문·사건 요약을 기록하지 않는다.
- 앱 종료 시 작업을 취소하고 종료를 기다린 뒤 DB 연결 풀을 해제한다. DB 행 잠금 대기 중 종료도 검증했다.
- 테스트 fixture는 요청 세션뿐 아니라 실제 lifespan의 engine·세션 팩토리도 테스트 DB로 바꾼다.
- 신규 테스트 8개를 추가해 임시 PostgreSQL 18에서 전체 127개가 통과했다(`127 passed in 19.00s`). Ruff 검사·포맷 검사와 `git diff --check`도 통과했다.
- 검증에는 실제 DB·행 잠금과 실제 앱 lifespan을 사용했다. 30초·10분 대기는 테스트에서 제어했으며 30초 대기 값과 기한 정책을 각각 확인했다.
- 테스트 컨테이너는 검증 후 종료·제거한다. 기존 프로젝트 DB·배포에는 적용하지 않았다.
- 자녀 사칭 확인 서버 범위는 완료했다. 실제 AI 입력 연결, FCM 발송·수신, Android 화면은 연동 후속이며, 앱 설치와 통화 지시 연관 분석은 현재 PRD-003 범위에서 제외한다.
