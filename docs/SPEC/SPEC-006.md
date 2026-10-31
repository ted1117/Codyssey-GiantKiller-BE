# SPEC-006: 위험 앱 설치 감지와 최근 의심 통화 표시

- 관련 요구사항: [PRD-006](../PRD/PRD-006.md)
- 연결 계약: [SPEC-004](SPEC-004.md), [SPEC-005](SPEC-005.md)
- 작성일: 2026-10-31 KST
- 상태: 구현 완료 (2026-10-31)

## 1. 범위

- 해커톤 시연용 최소 구현이다. 부모님 기기가 보낸 앱 설치 이벤트를 같은 요청에서 판정하고 결과를 반환한다.
- 위험 여부는 설치 정보만으로 판정한다. 통화 기록은 최근 의심 통화가 있었는지 표시하는 데만 쓰고 AI를 호출하지 않는다.
- 위험 앱 설치만 사건으로 저장하고 자녀에게 알린다. 위험하지 않은 설치는 DB 조회·저장·로그를 하지 않는다.
- DB 모델·마이그레이션·의존성은 변경하지 않는다. `event_type`·`classification`은 기존 문자열 컬럼에 새 값을 저장한다.
- Android 감지·경고창, SMS, 통화 지시 대조, 중복 수신 방지, 관련 통화 사건 연결은 범위 밖이다.

## 2. 공개 API: `POST /api/v1/actions/app-install`

Bearer JWT가 필요하다. 부모 사용자 ID는 인증에서 얻고 입력으로 받지 않는다.

### 2.1. 요청 (`AppInstallCreate`)

```text
family_id          UUID, 필수
package_name       str, 필수, 1~255자, 공백뿐인 값·NUL 거부
app_label          str, 필수, 1~200자, 공백뿐인 값·NUL 거부
is_unknown_source  StrictBool, 필수
occurred_at        AwareDatetime, 필수
```

- `extra="forbid"`로 알 수 없는 필드를 거부한다. 기존 `TextValue`·`nonblank` 검증을 재사용한다.
- 기획서 7.1 ④의 `device_id`·`timestamp`는 받지 않는다. 기존 분석 API와 같이 `occurred_at`을 쓴다.

### 2.2. 응답 (`AppInstallRead`, 200)

```text
matched       bool
event_id      UUID | null   # matched=false면 null
related_call  bool          # matched=false면 false
```

### 2.3. 오류

- 401: 인증 실패
- 403 `FAMILY_ACCESS_DENIED`, 404 `FAMILY_NOT_FOUND`: 기존 `FamilyService.require_member` 동작 그대로
- 403 `PARENT_REQUIRED`: 해당 가족의 `PARENT`가 아님
- 422: 입력 검증 실패

권한 오류는 판정·저장 전에 반환한다.

## 3. 판정

### 3.1. 위험 앱 설치

상수는 서비스 모듈에 둔다.

```text
REMOTE_PACKAGE_PREFIXES = ("com.teamviewer.", "com.anydesk.", "com.rsupport.")
RELATED_CALL_WINDOW = timedelta(hours=24)
```

`matched = package_name.startswith(REMOTE_PACKAGE_PREFIXES) or is_unknown_source`

DB 조회 없이 입력만으로 판정한다.

### 3.2. 최근 의심 통화

`matched=true`일 때만 조회한다.

`SecurityEventRepository.has_attention_call(family_id, since, until) -> bool`: 같은 `family_id`이면서
`event_type='CALL'`, `analysis_status='COMPLETED'`, `requires_attention IS TRUE`,
`since <= occurred_at <= until`인 사건이 있는지 `select(exists())`로 확인한다.
`since = install.occurred_at - 24시간`, `until = install.occurred_at`이다. 저장소는 조회만 한다.

## 4. 저장과 알림

### 4.1. 사건

`SecurityEventRepository.create()`의 기존 인자로 저장한다.

```text
event_type          "APP_INSTALL"
classification      "RISKY_APP_INSTALL"
risk_level          "HIGH"
summary             related_call=true:  f"최근 의심 통화 뒤 '{app_label}' 앱 설치가 감지됐어요."
                    related_call=false: f"위험할 수 있는 '{app_label}' 앱 설치가 감지됐어요."
requires_attention  True
analysis_status     COMPLETED
status              OPEN (모델 기본값)
parent_member_id    요청한 부모의 멤버 ID
occurred_at         install.occurred_at을 UTC로 변환
```

패키지명은 사건에 저장하지 않는다.

### 4.2. 서비스와 트랜잭션

`AppInstallService(session, push).report(user_id, body) -> AppInstallRead`

1. `transaction(session)` 안에서 `FamilyService.require_member`와 `PARENT` 확인 후 `matched`를 판정한다.
2. `matched=false`면 트랜잭션을 끝내고 `AppInstallRead(matched=False, event_id=None, related_call=False)`를 반환한다.
3. `matched=true`면 같은 트랜잭션에서 최근 의심 통화 조회, 사건 저장, 가족의 `CHILD` user_id 목록 조회를 한다.
4. commit 후 알림을 보내고 결과를 반환한다.

### 4.3. 알림

- 새 `PushMessageType.INSTALL_WARNING`을 추가한다. 스키마 ENUM이라 DB 변경은 없다.
- `app/services/push.py` `_MESSAGES`에 고정 문구를 추가한다.
  - 제목: '부모님 휴대폰 긴급 확인'
  - 본문: '위험할 수 있는 앱 설치가 감지됐어요. 바로 부모님께 연락해 주세요.'
- 자녀마다 `PushSend(user_id, event_id, INSTALL_WARNING)`를 순서대로 보내고, 실패는 `install_push_failed event_id=… user_id=… code=<예외 클래스명>`으로 기록한다. `EventService.create_alert`와 같은 방식이다.
- 부모에게는 보내지 않는다. 앱 이름·패키지명은 푸시에 넣지 않는다.

### 4.4. 로그

- 위험 앱 설치: `app_install_matched event_id=<id> related_call=<true|false>`
- 위험하지 않은 설치는 로그를 남기지 않는다.

## 5. 자녀 조회 공개

`SecurityEventRepository._visible()`에 분기를 추가한다.

```text
event_type == "APP_INSTALL" AND requires_attention IS TRUE
```

기존 세 분기는 그대로 둔다. 응답 필드·권한·정렬은 SPEC-005 13절과 같고, 상세의 `instructions`는 빈 목록이다.
대응 상태 변경(PATCH)도 같은 공개 조건을 쓰므로 자녀가 이 사건을 확인·해결로 바꿀 수 있다.

## 6. 계층과 영향 파일

- `app/schemas/action.py` (신규): `AppInstallCreate`, `AppInstallRead`
- `app/repositories/event.py`: `has_attention_call`, `_visible()` 분기 추가
- `app/services/app_install.py` (신규): `AppInstallService`, 판정 상수·저장·알림
- `app/schemas/push.py`, `app/services/push.py`: `INSTALL_WARNING` 유형과 문구
- `app/api/dependencies.py`: `get_app_install_service`, `AppInstallServiceDep` (`PushServiceDep` 재사용)
- `app/api/v1/actions.py` (신규): `APIRouter(prefix="/actions", tags=["actions"])`
- `app/api/router.py`: actions 라우터 등록
- `tests/test_app_install.py` (신규), `tests/test_api.py`(OpenAPI 경로 목록), `tests/test_event_queries.py`(공개 분기)
- 문서: PRD-006 체크, PRD-003 4절 보류 항목에 'PRD-006으로 이관' 표시, 이 문서 결과 절, PROGRESS

## 7. 검증

PRD-006 8절 수용 기준을 API 테스트로 확인한다.

- 원격제어 앱 + 최근 의심 통화: 사건 저장 값, `related_call=true`, 자녀 수만큼 `INSTALL_WARNING`, 부모 미발송
- 출처 불명 앱 + 의심 통화 없음: `matched=true`, `related_call=false`, 요약 문구 분기
- 일반 앱: 사건·푸시 0건, `matched=false`
- 정상 통화·다른 가족 의심 통화·24시간 넘은 의심 통화만 있음: `related_call=false`
- 발송 예외: 사건·200 유지, 로그에 예외 문구 없음
- 자녀·다른 가족 사용자: 403
- 자녀 목록·상세에 `APP_INSTALL` 사건 공개
- 독립 PostgreSQL 18에서 전체 테스트, Ruff 검사·포맷, `git diff --check`

## 8. 남은 확인

- 설치 시각을 클라이언트가 보내므로 잘못된 값은 `related_call`을 바꿀 수 있다. 이번에는 보정하지 않는다.
- Android의 `PACKAGE_ADDED` 수신, 경고창 표시, 실기기 FCM 수신은 연동 후 검증한다.

## 9. 구현 결과 (2026-10-31)

- 신규: `app/schemas/action.py`, `app/services/app_install.py`, `app/api/v1/actions.py`, `tests/test_app_install.py`
- 수정: `app/repositories/event.py`(`has_attention_call`, `_visible()` APP_INSTALL 분기), `app/schemas/push.py`·`app/services/push.py`(`INSTALL_WARNING`), `app/api/dependencies.py`, `app/api/router.py`, `tests/test_api.py`(OpenAPI 경로)
- 동작: `POST /api/v1/actions/app-install`이 부모 권한 확인 뒤 설치 정보만으로 판정한다. 위험하지 않으면 DB 조회·저장·로그 없이 `matched=false`를 반환한다. 위험하면 최근 24시간 의심 통화를 조회하고 `APP_INSTALL` 사건을 저장한 뒤, commit 후 자녀에게 `INSTALL_WARNING`을 한 명씩 보낸다. 발송 실패는 예외 클래스명만 기록하며 사건과 200 응답을 유지한다. 패키지명은 저장·로그하지 않는다.
- 테스트: 신규 7개, 독립 PostgreSQL 18 전체 219개 통과(`test_push.py`의 알림 유형 파라미터 테스트가 `INSTALL_WARNING`을 포함). Ruff 검사·포맷, `git diff --check` 통과.
