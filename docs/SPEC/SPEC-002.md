# SPEC-002: 시연용 문자·통화 AI 분석 연결

- 관련 요구사항: [PRD-002](../PRD/PRD-002.md)
- 연결 계약: [SPEC-003](SPEC-003.md), [SPEC-005](SPEC-005.md)
- 작성일: 2026-10-31 KST
- 상태: 1차 백엔드 구현·자동 검증 완료. 일반 요청은 AI 미설정 503이며 실제 AI 계약·연동은 미확정

## 1. 범위

해커톤 영상에서 준비된 문자와 짧은 녹음으로 기존 서비스를 연결한다.

- 두 분석 API 모두 요청 안에서 AI 응답을 기다린다.
- 기존 JWT로 요청자를 식별하고 대상 가족 하나의 `PARENT` 소속을 검사한다.
- 통화는 AI 호출 전에 사건을 저장하고 기존 `EventService`로 결과·실패를 기록한다.
- 문자는 자녀 사칭일 때 사건·가족 확인 요청·알림 요청을 저장한다.
- 통화 이력·별도 기기 인증·작업 큐·상태 조회·자동 재시도·복구는 PRD의 후속 범위를 유지한다.

사용자가 실제 AI 계약은 아직 없다고 확인했다. 아래 공개 API와 내부 연결은 백엔드 설계안이며 AI 서버가 이미 지원하는 요청·응답으로 취급하지 않는다.

## 2. 공개 API

두 API 모두 Bearer JWT가 필요하다. 부모 사용자 ID는 인증에서 얻고 입력으로 받지 않는다.
`family_id`는 클라이언트가 선택한 가족 하나이며 서버에서 소속·역할을 검사한다.

입력·권한 검증 후 AI 연결이 미설정이면 두 API 모두 사건 생성 전에
`503 {"detail": "AI_NOT_CONFIGURED"}`를 반환한다. 사건·가족 확인·알림을 생성하거나 가짜 결과를 반환하지 않는다.
AI 설정이 없어도 기존 서버는 기동할 수 있어야 하며, 테스트에서만 AI 응답을 주입한다.
이 조기 반환에서도 업로드 파일은 닫는다.

### 2.1. 문자: `POST /api/v1/alerts/check`

JSON 요청 모델 `AlertCheck`:

- `family_id: UUID`, 필수
- `occurred_at: datetime`, 타임존을 포함한 문자 발생 시각, 필수
- `text: str`, 앱에서 마스킹한 본문, 필수. 빈 값·공백만 있는 값·NUL은 거부한다.

200 응답 모델 `AlertCheckRead`:

- `result: AlertAnalysisResult`, 검증·정규화한 분석 결과
- `event_id: UUID | None`, 사칭 사건이 생성되었을 때만 값이 있다.
- `verification_id: UUID | None`, 가족 확인 요청이 생성되었을 때만 값이 있다.

사칭 이외 결과는 두 ID를 `null`로 반환한다. AI 호출·시간 초과·응답 검증 실패는 502와 고정 오류 코드를 반환한다.

### 2.2. 통화: `POST /api/v1/calls/analyze`

`multipart/form-data` 요청:

- `family_id: UUID`, 필수
- `occurred_at: datetime`, 타임존을 포함한 통화 발생 시각, 필수
- `file: UploadFile`, 동의된 시연용 녹음 한 파일, 필수

응답 모델 `CallAnalyzeRead`:

- `event_id: UUID`, 접수 때 저장한 사건 ID
- `analysis_status: COMPLETED | FAILED`
- `status: OPEN | NOT_REQUIRED`, 사건 대응 상태
- `result: CallAnalysisResult | None`
- `error_code: AI_REQUEST_FAILED | INVALID_ANALYSIS_RESULT | None`

분석 성공은 200이며 `result`를 채우고 `error_code`는 `null`이다.
AI 호출·시간 초과·응답 검증 실패는 기존 사건에 실패를 확정한 뒤 같은 응답 모델로 502를 반환한다.
이때 `analysis_status=FAILED`, `status=OPEN`, `result=null`이며 `event_id`를 유지한다.
별도 작업 ID나 상태 조회 API는 추가하지 않는다.

## 3. 서비스 처리

Router는 입력 검증·서비스 호출·응답 상태 설정만 담당한다.
새 `AnalysisService`가 AI 호출과 기존 서비스·저장소를 조합한다.

### 3.1. 통화

1. 파일·입력·부모 권한을 검사하고 AI 연결이 준비되었는지 확인한다. 미설정이면 공통 503 규칙으로 종료한다. 파일 크기 검사 후 `await file.seek(0)`으로 읽기 위치를 되돌린다.
2. `EventService.create_call(family_id, user.id, occurred_at)`을 호출한다. 기존 서비스가 부모 소속·역할을 확인하고 `PENDING` 사건을 commit한다.
3. `start_analysis(event_id)`로 `PROCESSING`을 별도 commit한다.
4. DB 트랜잭션·잠금이 없는 상태에서 AI를 호출하고 응답을 검증·변환한다.
5. 호출·시간 초과는 `fail_analysis(event_id, "AI_REQUEST_FAILED")`, 잘못된 응답은 `fail_analysis(event_id, "INVALID_ANALYSIS_RESULT")`로 기록한다.
6. 검증 성공 시에만 `complete_analysis(event_id, result)`를 호출한다. 정상은 `NOT_REQUIRED`, 위험·의심은 `OPEN`이며 기존 서비스가 결과 commit 후 해당 가족 자녀에게 `CALL_SUMMARY` 알림을 시도한다.
7. 결과·실패를 반환하고 `finally`에서 업로드 파일을 닫는다.

AI 예외 처리 범위는 AI 호출·결과 검증에 한정한다. DB 저장 오류를 AI 실패로 바꾸지 않는다.
자녀 알림 실패는 완료된 분석을 `FAILED`로 바꾸지 않는다. 실제 기기 수신 여부를 응답으로 보장하지 않는다.
통화 분석 결과만으로 가족 확인을 추가 생성하는 기능은 이번 범위에 포함하지 않는다.

### 3.2. 문자

1. 기존 가족 서비스로 소속을 확인하고 `PARENT` 역할을 검사한다. AI 연결 미설정이면 공통 503 규칙으로 종료한다. 준비되었으면 인증 조회를 포함한 DB 트랜잭션을 정리한 뒤 AI를 호출한다.
2. AI 응답을 `AlertAnalysisResult`로 검증·변환한다. 실패하면 사건이나 가족 확인 요청을 생성하지 않고 오류를 반환한다.
3. `classification=CHILD_IMPERSONATION`이면 1단계에서 검증한 부모 멤버 ID로 `SecurityEventRepository.create()`를 호출한다. 사건 유형은 `ALERT`, 초기 대응 상태는 `OPEN`이다.
4. 같은 세션에서 `VerificationService.create(event_id)`를 호출한다. 이 메서드의 기존 트랜잭션으로 사건·확인 요청·대상·알림 요청을 함께 commit하고, 생성 실패 시 함께 rollback한다.
5. 사칭이 아니고 `requires_attention=true`이면 `EventService.create_alert()`가 한 트랜잭션에서 `ALERT` 사건(`requires_attention=true`, `analysis_status=COMPLETED`, 상태 `OPEN`)을 저장하고 가족의 `CHILD` 사용자 ID를 수집한다. commit 후 자녀별로 `RISK_WARNING`(`event_id`만, `verification_id` 없음)을 한 건씩 발송하며 실패는 오류 코드만 로그에 남기고 결과를 바꾸지 않는다. 부모에게는 보내지 않는다.
6. 분석 결과와 생성된 ID를 반환한다. 위험 문자 사건은 `verification_id=null`이다. `requires_attention=false`인 사칭 이외 결과는 사건·알림 없이 결과만 반환한다.

문자 경로에서 사건은 기존 저장소의 flush 후 확인 생성 서비스에서 함께 확정한다. 확인 생성 전에 사건만 따로 commit하지 않는다.
가족 확인의 대상 선정·10분 만료·응답 집계는 기존 서비스를 그대로 사용한다.
`NotificationRequest`는 발송 의도 저장이며, 이번에는 자동 FCM 발송에 연결하지 않는다.

## 4. AI 연결 경계

구체적인 클래스 하나인 `AIClient`에 문자·통화 호출과 응답 변환을 모은다. 별도 provider 계층·Protocol·가짜 AI 서버는 추가하지 않는다.

내부 반환 계약:

- `analyze_alert(text)`는 `AlertAnalysisResult`를 반환한다. 필드는 `classification`, `requires_attention`, `risk_level`, `summary`다.
- `analyze_call(file)`은 기존 `CallAnalysisResult`를 반환한다. 위 필드와 `instructions`를 포함한다.
- 분류는 빈 값이 아닌 최대 64자, 위험 수준은 `None` 또는 최대 32자, 판정은 엄격한 불리언으로 기존 모델 기준을 따른다.
- 문자 요약은 `None`을 허용하고, 통화 지시는 없으면 빈 목록으로 정규화한다. 원문 전체를 응답이나 사건에 추가 저장하지 않는다.
- 실제 AI 분류·위험 수준·지시 유형의 허용값과 `requires_attention` 매핑은 AI 계약을 받을 때 정한다. 미지의 분류나 판정 누락을 임의의 정상값으로 바꾸지 않는다.
- 두 내부 결과 모델은 `classification=CHILD_IMPERSONATION`일 때 `requires_attention=true`를 요구한다. 이 일관성 검증은 AI 결과를 내부 모델로 변환할 때 실행하며, 테스트에서 주입하는 결과도 같은 모델을 사용한다.
- 사칭 분류와 `requires_attention=false`가 함께 오면 `INVALID_ANALYSIS_RESULT`로 거부한다. 통화는 기존 사건에 `FAILED`를 기록하고, 문자는 사건·가족 확인 요청을 생성하지 않는다. 위험 알림도 호출하지 않는다.
- `AlertAnalysisResult`도 허용 필드·타입·NUL을 검증한다. 외부 AI 응답을 공개 응답으로 그대로 전달하지 않는다.

AI 서버 주소·경로·인증·입력 필드·응답 JSON은 현재 미확정이다.
녹음을 보내 한 번에 STT·분석 결과를 받는지, 여러 호출을 조합하는지도 아직 정해지지 않았다.
백엔드 API 경로를 AI 서버 경로로 가정하거나 내부 모델을 실제 AI 응답 예시로 제시하지 않는다.

## 5. 파일·시간 제한

- 준비된 시연 파일 한 종류를 지원한다. 허용 형식·최대 바이트 수·AI 호출 시간 제한은 실제 시연 파일과 AI 계약을 확보한 뒤 정한다.
- 빈 파일은 422, 크기 상한 초과는 413, 미지원 형식은 415로 AI 호출·사건 생성 전에 거부한다.
- 크기는 실제 읽은 바이트를 기준으로 제한한다. 원본 보관소나 추가 녹음 저장 테이블은 만들지 않는다.
- 크기 검사를 위해 파일을 읽은 뒤에는 `await file.seek(0)`을 호출한다. `analyze_call(file)`에는 처음부터 읽을 수 있는 파일을 전달한다.
- 요청 처리 중 사용하는 업로드 파일은 성공·예외 경로 모두 닫고, AI 호출은 비동기 HTTP 요청으로 처리한다.
- 서버 강제 종료·클라이언트 연결 중단 이후 결과 전달·작업 복구는 보장하지 않는다. 정상 실행 중 포착한 AI 오류·시간 초과는 실패 기록 대상으로 한다.

제한값은 아직 확정되지 않았다. 긴 녹음 지원이나 별도 진행률 API로 범위를 넓히지 않는다.

## 6. 오류 응답

- 401: 기존 JWT 인증 실패
- 403: `FAMILY_ACCESS_DENIED`, `PARENT_REQUIRED`
- 404: `FAMILY_NOT_FOUND`
- 413: `AUDIO_TOO_LARGE`
- 415: `UNSUPPORTED_AUDIO_FORMAT`
- 422: 요청 필드·시각·빈 문자·빈 파일 검증 실패
- 502: `AI_REQUEST_FAILED`, `INVALID_ANALYSIS_RESULT`. 시간 초과도 `AI_REQUEST_FAILED`로 묶는다.
- 503: `AI_NOT_CONFIGURED`. AI 연결 미설정으로 사건 생성 전에 거부하며 통화 사건 ID도 생성하지 않는다.
- 500: DB 등 예상하지 못한 내부 오류. 성공 결과나 AI 분석 실패로 위장하지 않는다.

통화의 AI 실패는 2.2절 응답 모델로 사건 ID를 함께 반환한다.
그 외 서비스 오류는 기존 `ServiceError`의 `{"detail": "오류 코드"}` 형식을 재사용한다.
요청 검증 오류는 기존 FID API의 방식처럼 이 분석 Router에서 입력 원문을 제외하고 반환한다.
문자·음성·AI 응답·검증 예외 원문은 로그에 남기지 않는다.

## 7. 영향 파일과 구현 순서

백엔드 입력·응답과 기존 서비스 연결은 아래 계획으로 준비할 수 있다.
실제 HTTP 호출·응답 매핑과 파일·시간 제한은 AI 계약·시연 파일 확인 후 완성한다.
첫 구현에서는 일반 요청에 공통 503 규칙을 적용하고, 테스트용 AI 응답을 주입해 서비스 연결을 검증한다.

1. `app/schemas/analysis.py` 신규, `app/schemas/event.py` 보완: 문자 입력·내부 결과, 문자·통화 응답과 사칭 분류·판정 일관성 검증
2. `app/core/ai.py` 신규: 첫 구현은 미설정 상태와 테스트 주입 경계를 정의한다. 실제 AI 호출·시간 제한·응답 매핑은 계약 확인 후 연결한다.
3. `app/services/analysis.py` 신규: 입력·부모 권한과 기존 사건·가족 확인 서비스 조합
4. `app/api/v1/analysis.py` 신규: 분석 API 두 개와 입력 원문 없는 검증 오류 응답
5. `app/core/config.py`, `app/api/dependencies.py`, `app/api/router.py`: AI 설정과 `Annotated` dependency·라우터 연결
6. `tests/test_analysis.py`, 필요한 범위의 `tests/test_api.py`: 연결 동작과 OpenAPI 검증
7. `docs/SPEC/SPEC-002.md`, `docs/PROGRESS.md`: 구현 결과·실제 검증 범위 기록

DB 모델·마이그레이션은 추가하지 않고 기존 사건·확인·알림 모델을 사용한다.
HTTP 클라이언트는 현재 테스트에서 사용하는 `httpx`를 후보로 둔다. 직접 의존성 추가가 필요하면 구현 영향 범위에 `pyproject.toml`·`uv.lock`을 명시하고 `uv add`로 반영한다.
1차 구현에서는 의존성·DB 모델·마이그레이션·`.env`를 변경하지 않았다. AI 설정 필드와 HTTP 클라이언트 추가는 실제 계약 연결 단계로 남긴다.

## 8. 검증과 남은 확인

자동 검증은 AI 클라이언트 반환값·예외를 주입하고 테스트 전용 PostgreSQL에서 진행한다.

- AI 호출 시 다른 DB 세션에서 통화 사건의 선저장·`PROCESSING`을 확인한다.
- 기본 AI 연결 미설정 상태에서 유효한 문자·통화 요청이 503이며 사건·가족 확인·알림이 생성되지 않는지 확인한다.
- 정상은 사건 유지·알림 없음, 위험·의심은 결과·지시 공동 저장 후 가족 자녀 알림을 확인한다.
- AI 호출·시간 초과·결과 검증 실패는 같은 사건의 `FAILED`와 사건 ID가 포함된 502 응답을 확인한다.
- 타 가족·자녀 역할·잘못된 입력은 AI 호출·사건 생성을 하지 않는지 검사한다.
- 사칭 문자는 사건·확인·알림 요청의 공동 저장과 생성 실패 시 rollback을 검사한다.
- 사칭 분류와 `requires_attention=false` 조합을 결과 모델이 거부하는지 검사한다. 변환 검증 실패 시 통화는 기존 사건에 `FAILED`를 기록하고 문자 사건·가족 확인·위험 알림은 생성하지 않는지 확인한다.
- 파일 크기 검사 후 AI 클라이언트가 입력 파일 전체를 처음부터 읽을 수 있는지 확인한다.
- 파일 거부·AI 실패·DB 오류를 포함해 업로드 파일이 닫히는지 확인한다.
- 기존 가족 확인·통화 사건·FID·OpenAPI 검증을 함께 실행한다.

## 9. 1차 구현 결과 (2026-10-31)

- 분석 API 두 개와 요청·응답 모델, `AnalysisService`, 미설정 `AIClient`, dependency·라우터 연결을 구현했다.
- 기본 클라이언트는 503을 반환한다. 테스트에서만 AI 결과·예외와 알림 서비스를 주입해 통화 선저장·정상/위험/실패 처리와 문자 사건·확인·알림 요청의 공동 저장을 검증했다.
- 사칭 분류·판정 모순 거부, 파일 읽기 위치 복구·닫기, 권한 거부·입력 원문 없는 422·문자 저장 롤백을 검증했다. OpenAPI 예상 경로에 새 API를 반영했다.
- 독립 PostgreSQL 18에서 신규 13개 포함 전체 `181 passed in 25.59s`. DeprecationWarning을 오류로 처리했다.
- 파일은 청크 단위로 읽어 빈 파일을 거부하고 처음으로 되돌린다. 허용 형식·크기 상한·AI 시간 제한은 실제 계약과 시연 파일이 없으므로 아직 적용하지 않았다.
- 실제 HTTP 호출·분류 매핑·STT 및 FCM·Android 수신은 미구현 또는 미검증이다. PRD-002 전체 완료로 처리하지 않는다.

## 10. 위험 문자 사건화 (2026-10-31)

사용자 승인으로 PRD-002 5.2의 비사칭 위험 문자 사건화를 구현했다. 사칭 경로는 변경하지 않았다.

- `SecurityEventRepository.create()`에 키워드 전용 `requires_attention`·`analysis_status`(기본 None)를 추가했다.
- `EventService.create_alert()`와 `AnalysisService.check_alert()` 분기를 추가했다. 문자 원문·요약·예외 문구는 로그에 남기지 않는다.
- 자녀 공개 조건은 SPEC-005 14절을 따른다. 모델·마이그레이션·의존성은 변경하지 않았다.
- 검증: 위험 문자 사건 저장값, 확인·알림 요청 0건, 자녀별 `RISK_WARNING` 발송, 발송 실패 시 사건·200 유지와 로그 비노출. 독립 PostgreSQL 18 전체 `212 passed`, Ruff 통과.
