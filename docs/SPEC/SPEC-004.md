# SPEC-004: 알림 관리

- 관련 요구사항: [PRD-004](../PRD/PRD-004.md)
- 작성일: 2026-10-31 KST
- 상태: 독립 FID 관리·발송 서비스 구현 및 자동 테스트 완료, 실제 기기 연동 미검증
- 범위: FCM FID 관리와 독립적인 내부 발송 서비스

## 1. 합의와 범위

채팅에서 합의한 내용은 다음과 같다.

- Firebase Admin SDK로 FCM을 발송한다.
- 사용자당 여러 앱의 FID를 저장하고 해당 사용자의 모든 등록 FID으로 발송한다.
- 별도 기기 등록 기능과 기기명·모델·OS 정보는 만들지 않는다.
- 로그인한 앱이 FCM FID를 서버에 전달해 사용자와 연결한다.
- 서버가 제목·안내 문구를 정하고, 앱은 이를 거의 그대로 표시한다.
- `notification`과 `data`를 함께 보낸다. 사건 ID는 표시용 문구가 아니라 화면 이동에 사용한다.
- 가족 확인·사건 분석 서비스와의 호출 연결은 이번에 하지 않는다.
- 최대한 단순하게 구현한다.
- 후속 사용자 지시에 따라 deprecated FCM 등록 토큰 대신 FCM에 등록된 Firebase Installation ID(FID)를 사용한다.

아래 API 경로·필드·오류 처리와 운영 기본값은 위 합의를 구현하기 위한 선택이다.
사용자의 구현 승인에 따라 최소 구성으로 구현했으며, 운영 고도화 정책까지 확정한 것으로 취급하지 않는다.

이번 설계에 포함한다.

- 로그인 사용자 자신의 FCM FID 등록·갱신·연결 해제
- 대상 사용자 ID를 받는 내부 FCM 발송 서비스
- 메시지 유형별 고정 문구와 사건 ID·확인 요청 정보의 페이로드
- 무효 FID 정리와 FID별 성공·실패 로그
- 독립 테스트와 실제 테스트 기기 발송 검증 절차

다음은 후속이다.

- `VerificationService`의 발송 호출과 기존 `NotificationRequest` 자동 처리
- 실제 AI 분석 결과·사건 서비스 연결과 가족·역할에 따른 수신자 선정
- Android 알림 UI·오버레이·응답 버튼·화면 이동 구현
- 공개 푸시 발송 API, 별도 큐·주기 발송 작업·발송 이력 테이블
- 애플리케이션 수준 자동 재시도·서버 재시작 후 발송 복구·SMS 대체 안내

이 단계만으로 PRD-004 전체 수용 기준을 완료한 것으로 표시하지 않는다.

## 2. 앱과 서버의 FID 계약

1. 앱이 기존 로그인 API로 JWT를 받는다.
2. 앱이 FCM의 `onRegistered(installationId)`에서 받은 현재 FID를 인증된 FID 등록 API로 보낸다.
3. 서버가 인증된 사용자와 FID를 연결한다. 사용자 ID는 요청 본문으로 받지 않는다.
4. 앱 실행·로그인 후 로그인 상태라면 현재 등록 FID를 서버에 보내고, `onRegistered`가 다시 호출될 때도 반영한다.
5. 로그아웃·계정 전환 전에는 JWT와 로컬 로그인 상태를 지우기 전에 해당 FID의 사용자 연결을 해제한다. Firebase installation 자체를 삭제하는 작업은 아니다.

서버의 주기적인 FID 갱신 작업은 두지 않는다. FID는 Firebase Installations가 관리하며 앱은 FCM 등록 완료·갱신 콜백의 값을 서버에 반영한다.
AndroidManifest의 `firebase_messaging_installation_id_enabled=true`를 설정해야 한다.
자동 초기화가 비활성화된 앱은 `FirebaseMessaging.getInstance().register()`로 등록을 요청하고 콜백에서 FID를 받는다.
FCM 등록 전 Firebase Installations에서 ID만 얻은 상태로는 수신 준비 완료로 취급하지 않는다.
기존 등록 토큰을 `fid` 필드에 넣거나 서버에서 문자열 이름만 바꿔 변환하지 않는다.
FID 원문은 FCM 발송에 필요하므로 DB에 저장하되 API 응답·URL·로그에는 노출하지 않는다.

현재 인증은 JWT이며 서버 측 로그아웃·즉시 폐기가 없다.
따라서 등록된 FID는 실시간 로그인 세션 목록이 아니며, JWT 만료만으로 자동 삭제되지 않는다.
연결 해제 요청이 실패하면 이전 계정의 푸시 연결이 남을 수 있으므로 앱은 실패를 무시하지 않고 재시도할 수 있어야 한다.
이미 FCM에 접수된 알림은 연결 해제만으로 회수할 수 없다.

### 2.1. FID 등록·갱신

`PUT /api/v1/users/me/push-registrations`, 인증 필요, 성공 시 `204 No Content`.

```json
{
  "fid": "current-fcm-fid",
  "previous_fid": "previous-fcm-fid"
}
```

- `fid`는 필수이며 빈 문자열·공백만 있는 값을 거부한다. FID 원문은 임의로 변환하지 않는다.
- `fid`·`previous_fid`의 공통 검증에서 PostgreSQL TEXT에 저장할 수 없는 U+0000(NUL)을 거부한다. DB 접근 전에 원문 없는 422로 응답한다.
- 입력은 UTF-8 기준 최대 2048바이트다. 이는 FID UNIQUE 인덱스에 과도한 입력을 저장하지 않기 위한 서버 제한이며 FCM의 공식 FID 길이 규격을 의미하지 않는다. `previous_fid`에도 같은 제한을 적용한다.
- `previous_fid`는 선택이다. 앱이 FID 변경 전 값을 알고 있으면 함께 보낸다.
- 같은 사용자·FID를 다시 등록하면 중복 행 없이 등록 시각만 갱신한다.
- 같은 FID가 다른 사용자에게 연결돼 있으면 현재 로그인 사용자로 연결을 옮긴다. 계정 전환 후 이전 사용자와 동시에 연결하지 않는다.
- `previous_fid`는 현재 사용자의 FID이고 새 FID와 다를 때만 삭제한다.
- 새 FID 등록과 이전 FID 삭제는 한 트랜잭션으로 확정한다.
- FID 전체에 UNIQUE 제약과 DB upsert를 사용해 동시 등록에서도 행 하나만 유지한다.
- FID 외 별도 기기 식별자는 받지 않는다. 이전 FID를 알 수 없으면 같은 앱의 구·신 FID를 서버가 즉시 매칭할 수 없으며, 남은 구 FID는 무효 발송 응답 시 정리한다.

### 2.2. FID 연결 해제

`DELETE /api/v1/users/me/push-registrations`, 인증 필요, 성공 시 `204 No Content`.

```json
{
  "fid": "current-fcm-fid"
}
```

- FID를 URL에 넣지 않고 JSON 본문으로 받는다.
- 현재 사용자와 일치하는 FID만 삭제한다. 다른 기기의 FID에는 영향을 주지 않는다.
- 없는 FID·이미 삭제된 FID·다른 사용자에게 연결된 FID는 모두 204다. 소유 여부를 노출하지 않는다.
- 등록·해제 요청 모두 정의되지 않은 필드는 거부한다.
- 인증 실패·만료·비활성 계정은 기존 인증 정책대로 401, 잘못된 본문은 422다.
- 두 FID API의 422 응답은 `type`·`loc`·`msg`만 반환해 검증 오류에서도 입력 FID 원문을 제외한다.
- FID 목록 조회나 별도 기기 관리 API는 만들지 않는다.

## 3. DB 모델

기존 PostgreSQL·UUID·타임존이 있는 UTC 시각을 유지한다.

```text
PushRegistration (push_registrations)
id           UUID PK
user_id      UUID FK → users.id, NOT NULL, INDEX
fid          TEXT, NOT NULL, UNIQUE
updated_at   UTC datetime, NOT NULL
```

- 한 사용자는 여러 FID를 가질 수 있고, 한 FID는 한 사용자에게만 연결한다.
- `updated_at`은 마지막 등록·연결 변경 시각이다.
- 별도 Device 모델·활성 플래그·운영체제·세션 테이블은 추가하지 않는다.
- 저장소는 upsert·사용자별 조회·조건부 삭제만 담당하고 commit하지 않는다.
- 사용자 FID 변경 서비스에서 기존 `transaction()`으로 commit·rollback을 처리한다.
- 기존 `NotificationRequest`와 `NotificationType` DB 모델은 변경하지 않는다.

## 4. 메시지 계약

내부 입력은 다음과 같다. 외부 클라이언트가 임의 대상이나 문구로 발송하는 API는 제공하지 않는다.

```text
PushSend
user_id           UUID
event_id          UUID
message_type      PushMessageType
verification_id   UUID | None
```

`PushMessageType`은 스키마의 ENUM으로 정의한다. 기존 DB ENUM을 확장하지 않는다.
일반 유형은 `RISK_WARNING`, `CALL_SUMMARY`, 가족 확인 유형은 기존 5종 이름을 그대로 사용한다.
가족 확인 유형에는 `verification_id`가 필수이며, 일반 유형에서는 생략한다.
제목·본문·응답 액션은 서버의 유형별 고정 매핑에서 생성한다.

문구 초안:

- `RISK_WARNING`: 제목 '위험 주의 안내', 본문 '주의가 필요한 상황이에요. 앱에서 내용을 확인해주세요.'
- `CALL_SUMMARY`: 제목 '통화 요약 안내', 본문 '확인할 통화 요약이 있어요. 앱에서 확인해주세요.'
- `VERIFY_REQUEST`: 제목 '메시지 발송 확인 요청', 본문 '부모님이 받은 메시지를 직접 보내셨는지 확인해주세요.'
- `VERIFY_WAITING`: 제목 '가족에게 확인 중이에요', 본문 '송금·앱 설치·인증번호 전달을 잠시 멈춰주세요.'
- `VERIFY_CONFIRMED`: 제목 '자녀가 발송을 확인했어요', 본문 '메시지나 링크의 안전을 보장하는 것은 아니에요.'
- `VERIFY_DENIED`: 제목 '가족 전원이 발송을 부인했어요', 본문 '위험 행동을 멈추고 기존에 알고 있던 연락처로 직접 확인해주세요.'
- `VERIFY_EXPIRED`: 제목 '가족에게 확인하지 못했어요', 본문 '기존에 알고 있던 연락처나 다른 가족을 통해 확인해주세요.'

통화 요약 원문이나 사건 요약을 본문에 복사하지 않는다. `CALL_SUMMARY`는 요약 도착 안내다.
`VERIFY_CONFIRMED`를 안전 보증으로, `VERIFY_DENIED`를 사칭 확정으로 표현하지 않는다.

FCM 메시지 예시:

```json
{
  "notification": {
    "title": "메시지 발송 확인 요청",
    "body": "부모님이 받은 메시지를 직접 보내셨는지 확인해주세요."
  },
  "data": {
    "event_id": "<event-uuid>",
    "message_type": "VERIFY_REQUEST",
    "verification_id": "<verification-uuid>",
    "actions": "[\"SENT\",\"NOT_SENT\"]"
  }
}
```

- `data`의 값은 모두 문자열이다. 해당하지 않는 선택 키는 생략한다.
- `actions`는 `VERIFY_REQUEST`에만 포함하며, 기존 응답 값 `SENT`·`NOT_SENT`를 JSON 문자열로 전달한다.
- 응답 액션 정보가 자동으로 Android 알림 버튼을 생성하지는 않는다. 버튼·응답 화면은 앱 구현이다.
- 앱은 알림 선택 시 ID를 이용해 화면을 열고 인증된 API에서 최신 상태·권한을 확인한다. 푸시 데이터만으로 응답이나 상태 변경을 실행하지 않는다.
- 사건 상세 공개 API는 현재 후속이므로 실제 사건 화면 연결 완료로 취급하지 않는다.

## 5. 발송 흐름과 실패 처리

`PushService.send(PushSend)`는 내부 호출용 async 메서드로 둔다.

1. 대상 활성 사용자와 등록 FID를 조회하고 고정 페이로드를 만든다.
2. DB 조회 세션을 닫은 뒤 `asyncio.to_thread`로 초기화·자격 증명 조회·인증 토큰 발급/갱신·동기 `send_each` 전체를 worker thread에서 실행한다. 네트워크 응답 동안 DB 트랜잭션을 열어 두지 않는다.
3. FID별 결과를 집계하고 무효 FID만 별도 트랜잭션에서 삭제한다.
4. 호출자에게 성공 수·실패 수·건너뜀 사유를 반환하고 로그를 기록한다.

단순한 기본안:

- FID이 없거나 사용자가 비활성이면 발송 없이 건너뛰고 사유를 남긴다.
- 여러 FID는 최대 500개씩 나눠 보낸다. 일부 실패가 다른 FID의 성공을 취소하지 않는다.
- 애플리케이션 수준 자동 재시도·영속 발송 대기열은 만들지 않는다.
- async 호출도 완료까지 기다리므로 호출한 API 응답이 발송 시간만큼 늦어진다. 응답과 분리한 백그라운드 발송은 후속이다.
- 호출부는 통화 분석 완료(`EventService`)와 가족 확인(`VerificationService`)이다. 가족 확인은 알림 요청을 저장한 트랜잭션이 commit된 뒤 같은 요청의 대상에게 수신자별로 한 번 발송을 시도한다. 롤백된 작업은 발송하지 않는다.
- `NotificationRequest`를 다시 조회해 미발송 건을 보내는 작업은 만들지 않는다.
- 프로세스가 발송 도중 종료되면 미발송 건을 자동 복구하지 않으며, 재호출 시 중복 발송될 수 있다.
- async 호출을 취소해도 이미 실행 중인 worker thread의 발송은 중단을 보장하지 않는다.
- FCM 접수 성공은 기기 수신·표시·사용자 확인 성공이 아니다.
- 사건·가족 확인 상태는 이 서비스에서 수정하지 않는다. FCM 실패가 기존 사건을 삭제하거나 롤백하지 않는다.

무효 FID와 나머지 오류를 구분한다.

- SDK의 `UnregisteredError`는 해당 FID 삭제 대상으로 처리한다.
- 일반 `INVALID_ARGUMENT`는 페이로드 오류일 수도 있으므로 일괄 FID 삭제 근거로 사용하지 않는다.
- 인증·권한·다른 Firebase 프로젝트·네트워크·일시 서버 오류는 실패로 기록하고 FID를 유지한다.
- 무효 FID 삭제 시 조회 당시의 `id`·`user_id`·`updated_at`이 일치하는 행만 삭제해, 발송 중 재등록·계정 전환된 연결을 지우지 않는다.
- 로그에는 대상 사용자 ID·FID 행 ID·사건 ID·메시지 유형·성공/실패·고정 오류 코드를 남긴다. FID 원문·SDK 예외 원문·민감한 원문은 남기지 않는다.
- 무효 FID 정리 DB 오류도 기록한다. 이미 성공한 FCM을 되돌리거나 성공 결과를 재발송하지 않는다.

## 6. Firebase 설정과 Android 계약

- `firebase-admin`은 현재 `pyproject.toml`에 있으므로 새 의존성을 추가하지 않는다.
- Firebase Admin SDK는 Application Default Credentials를 사용한다.
- 비 Google 환경에서는 실행 환경의 `GOOGLE_APPLICATION_CREDENTIALS`가 가리키는 외부 자격 증명 파일로 설정할 수 있다. 키 파일을 저장소·이미지에 포함하지 않는다.
- FCM 클라이언트는 최초 발송 시 이름이 있는 Firebase 앱을 초기화하고 재사용한다. 초기화뿐 아니라 SDK 인증·발송 전체를 스레드에서 수행하고 SDK의 HTTP timeout은 10초로 설정한다. 전체 배치의 완료 시간이나 기기 전달 시간을 10초로 보장하는 값은 아니다.
- import·FID API 호출·일반 앱 기동에 Firebase 자격 증명을 필수로 요구하지 않는다.
- 자격 증명 미설정·초기화 실패는 발송 실패로 기록한다. FID를 삭제하거나 발송 성공으로 표시하지 않는다.
- `.env`는 읽거나 수정하지 않는다. 실행 설정과 컨테이너 마운트 방법은 구현 시 README에 안내한다.
- Android 팀은 같은 Firebase 프로젝트의 FID를 사용하고 알림 권한·채널·수신 콜백·선택 후 화면 이동을 처리한다.
- 기존 `AndroidConfig` 방식에서는 백그라운드의 `notification + data`는 시스템 알림으로 표시되고 데이터는 선택 후 앱에 전달된다. 포그라운드 표시에는 앱 처리가 필요하다.
- 현재 설치된 Admin SDK 7.7.0의 `Message(fid=..., notification=..., data=...)`와 동기 `send_each`를 사용한다. `send_each_async`도 인증에 동기 I/O를 사용하므로 이벤트 루프에서는 호출하지 않는다. `AndroidConfigV2`는 지정하지 않는다.
- 서버는 기존 등록 토큰 입력·발송과의 호환 분기를 두지 않는다. 이전 `token`·`previous_token` 요청 키는 422로 거부한다.

## 7. 영향 파일과 구현 순서

아래 1~3단계를 구현했다. 실제 기기 발송·Android 수신 검증은 남아 있다.

1. FID 모델·저장소·마이그레이션
   - 신규 `app/models/push_registration.py`, `app/repositories/push_registration.py`
   - 신규 `migrations/versions/0003_push_registrations.py`
   - 수정 `app/models/__init__.py`
2. FID API와 인증·트랜잭션
   - 신규 `app/schemas/push.py`, `app/services/push_registration.py`, `app/api/v1/push_registrations.py`
   - 수정 `app/api/dependencies.py`, `app/api/router.py`
3. 내부 FCM 발송과 검증
   - 신규 `app/core/fcm.py`, `app/services/push.py`
   - 신규 `tests/test_push_registrations.py`, `tests/test_push.py`
   - 필요한 범위에서 수정 `tests/test_api.py`, `tests/conftest.py`, `README.md`
   - 구현 결과를 이 SPEC과 `docs/PROGRESS.md`에 반영

`VerificationService`·기존 알림 요청 저장소·기존 주기 만료 작업은 변경하지 않는다.
기존 DB 마이그레이션 적용·배포·실제 FCM 발송은 별도 실행 대상 확인 후 진행한다.
기존 토큰용 0003은 이 작업에서 만든 미추적·미배포 파일이므로 FID용 `0003_push_registrations`로 교체했다.
기존 프로젝트 DB를 변경하지 않았고, 저장된 등록 토큰을 FID로 변환하는 데이터 마이그레이션은 제공하지 않는다.

## 8. 검증과 남은 연동

- FID 등록 재호출·동시 등록에서 중복되지 않는지, 사용자당 여러 FID가 유지되는지 확인한다.
- 계정 전환·이전 FID 교체·자기 FID만 해제·인증 실패·입력 검증을 확인한다.
- 무효 응답 후 삭제와 발송 중 재등록된 FID 보존을 확인한다.
- 유형별 문구·문자열 데이터·사건 ID·확인 요청 ID·응답 액션과 민감 원문 제외를 확인한다.
- 가짜 FCM 클라이언트로 전체 성공·부분 실패·FID 없음·비활성 계정·자격 증명 오류를 확인한다.
- 서비스 호출이 없으면 기존 알림 요청이 자동 발송되지 않는지 확인한다.
- 가족 확인 생성·응답 확정·만료에서 commit 후 수신자·유형별 발송 시도, 재호출·롤백 시 미발송, 발송 예외 시 결과 유지를 확인한다.
- 마이그레이션과 DB 검증은 기존 DB와 분리한 테스트 PostgreSQL에서 진행한다.
- Ruff·해당 테스트·전체 회귀 검증 결과는 10절에 기록한다.
- 실제 Firebase 테스트 기기로 발송하고 Android의 포그라운드·백그라운드 수신 및 알림 선택을 별도 검증한다.

PRD-004에 추가 반영할 정책 제안은 자동 재시도·대체 안내 제외, 앱 시작·FID 변경 시 등록,
FID 연결 해제 실패 처리다. PRD에는 사용자가 지시한 FID 전환만 반영하고 이 운영 기본값은 본 문서에 기록한다.
가족 확인 연결 시에는 이미 확정·만료된 요청을 뒤늦게 보내지 않는 검사와 10분 응답 기한에 맞는 FCM TTL도 정해야 한다.

## 9. 공식 참고 자료

- [Android FCM 수신과 앱 상태별 처리](https://firebase.google.com/docs/cloud-messaging/android/receive-messages)
- [Android FID 등록 설정과 onRegistered](https://firebase.google.com/docs/cloud-messaging/android/get-started#access_the_firebase_installation_id)
- [FCM 등록 FID 관리와 무효 FID 처리](https://firebase.google.com/docs/cloud-messaging/manage-tokens)
- [Firebase Admin Python messaging API](https://firebase.google.com/docs/reference/admin/python/firebase_admin.messaging)
- [Admin SDK 초기화와 자격 증명 설정](https://firebase.google.com/docs/admin/setup)

## 10. 구현과 검증 결과

- 최소 `PushRegistration` 모델·저장소와 `0003_push_registrations` 마이그레이션을 추가했다.
- 인증된 사용자 자신의 FID 등록·교체·연결 해제 API와 비공개 `PushService`를 구현했다.
- `PushService`는 호출자의 세션 대신 전달받은 세션 팩토리로 별도 세션을 사용한다. FID 조회 후 세션을 닫고 발송하며, 무효 FID 정리도 별도 트랜잭션이다.
- 표시 문구 7종·ID·응답 액션, 최대 500개 분할, 부분 실패, 무효 FID 조건부 삭제와 원문 없는 로그를 구현했다.
- `README.md`에 FID API·로그아웃 계약·ADC·외부 자격 증명 파일의 컨테이너 마운트 방법을 기록했다.
- 신규 테스트 22개가 통과했다. 등록 멱등성·다중 앱·계정 전환·이전 FID 교체·자기 FID만 해제·동시 등록·롤백·인증·입력 원문 보호·마이그레이션 왕복을 검증했다.
- 가짜 FCM과 실제 SDK 메시지 객체로 페이로드·부분 실패·사건 보존·통신 중 열린 DB 트랜잭션 없음·발송 중 재등록/계정 전환·500개 분할·정리 DB 오류·자동 연결 없음도 검증했다.
- FID 전환 후 새 PostgreSQL 18 테스트 컨테이너에서 `149 passed in 21.14s`, 경고 0개를 확인했다. `-W error::DeprecationWarning`으로 deprecated API 사용을 실패 처리해 검증했다.
- SDK 메시지의 실제 JSON에 `fid`가 있고 `token`이 없는지 확인했다. API의 기존 `token`·`previous_token` 입력 거부도 검증했다.
- PR #3 검토 반영: `_get_app`만 스레드에서 실행하던 구조를 인증·발송 전체로 확장했다. 초기화와 인증 포함 발송 각각의 대기 중 다른 async 작업이 진행되는 회귀 테스트 2개를 추가했다.
- 스레드 수정 후 새 PostgreSQL 18 테스트 컨테이너에서 전체 `151 passed in 19.53s`, 경고 0개를 확인했다. Ruff 검사·포맷도 통과했다.
- NUL 검증 수정 후 PUT의 `fid`·`previous_fid`와 DELETE의 `fid`가 원문 없는 422를 반환하고 기존 데이터를 보존하는 회귀 테스트 3개를 추가했다. 새 PostgreSQL 18에서 전체 `154 passed in 18.86s`, 경고 0개와 Ruff 검사·포맷 통과를 확인했다.
- `uv run --no-sync ruff check app migrations tests`와 `uv run --no-sync ruff format --check app migrations tests`, `git diff --check`가 통과했다.
- 실제 Firebase 호출·Android 기기 수신·기존 프로젝트 DB 마이그레이션·배포는 실행하지 않았다. `.env`는 읽거나 수정하지 않았다.
- 2026-10-31 가족 확인 연결: `VerificationService`에 선택적 `PushService`를 주입했다. 생성 시 자녀 `VERIFY_REQUEST`·부모 `VERIFY_WAITING`(대상 없으면 `VERIFY_EXPIRED`), 확정 시 부모 `VERIFY_CONFIRMED`·`VERIFY_DENIED`·`VERIFY_EXPIRED`를 commit 후 발송한다. API는 `get_push_service` dependency, 만료 작업은 앱 세션 팩토리로 주입한다. 발송 예외는 `verification_push_failed`로 기록하고 확정 결과를 유지한다.
- 미정: 10분 기한에 맞춘 FCM TTL, 발송 지연이 큰 경우 이미 확정된 요청의 `VERIFY_REQUEST`가 늦게 도착하는 경우의 앱 처리. 앱은 알림 선택 시 상태 조회 API로 최신 상태를 확인한다.
- 신규 `tests/test_verification_push.py` 8개 추가, 새 PostgreSQL 18 테스트 컨테이너에서 전체 `206 passed`, Ruff 검사·포맷 통과.
