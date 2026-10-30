# GiantKiller Backend

Python 3.14, FastAPI, PostgreSQL 18, SQLAlchemy 2.0, FastAPI Users를 사용합니다.
회원·가족·초대 API 설계는 [SPEC-001](docs/SPEC/SPEC-001.md)에 있습니다.

## 실행 설정

기존 DB 설정에 `AUTH_SECRET`을 추가해야 합니다. 최소 32자이며 다음 명령으로 생성할 수 있습니다.
기존 `.env`의 다른 값을 덮어쓰지 않고 생성한 값을 직접 추가합니다.

```sh
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

- `AUTH_TOKEN_LIFETIME_SECONDS`: JWT 유효 시간. 기본 3600초입니다.
- `INVITATION_BASE_URL`: 앱·웹 초대 화면의 URL prefix. 예: `https://app.example.com/invite`
- 초대 주소를 설정하지 않으면 요청을 받은 서버의 `/api/v1/invitations/{token}` 주소를 반환합니다.
  이는 로그인 후 호출하는 API 미리보기 주소입니다. 앱 설치·가입 화면으로 연결하는 처리는 클라이언트에서 구현합니다.

## Docker 실행

실행 설정을 준비한 뒤 대상 DB를 확인하고 마이그레이션을 적용합니다.
마이그레이션은 API 시작 시 자동 실행하지 않습니다.

```sh
docker compose build api
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose up -d api
```

Swagger UI는 `/docs`, 기존 상태 확인은 `/health`입니다.
이미지에는 Alembic 설정과 마이그레이션도 포함됩니다.

## 회원·가족·초대

- 회원가입: `POST /api/v1/auth/register`, JSON으로 `email`, `password`, `name` 전달
- 로그인: `POST /api/v1/auth/login`, form으로 `username=이메일`, `password` 전달
- 인증: `Authorization: Bearer <access_token>`, 내 정보 조회는 `GET /api/v1/users/me`
- 가족 생성·목록·상세: `/api/v1/families`, `/api/v1/families/{family_id}`
- 초대 생성: `POST /api/v1/families/{family_id}/invitations`, JSON으로 `role=PARENT/CHILD` 전달
- 초대 취소: `DELETE /api/v1/families/{family_id}/invitations/{invitation_id}`
- 초대 미리보기·수락: `GET /api/v1/invitations/{token}`, `POST /api/v1/invitations/{token}/accept`

가족 생성자는 대표 자녀가 되고, 대표만 다른 부모·자녀를 초대할 수 있습니다.
초대는 24시간·1회 사용이며, 가족당 부모는 최대 2명입니다. 계정은 여러 가족에 참여할 수 있습니다.
JWT가 만료되면 다시 로그인합니다. 갱신·서버 측 토큰 폐기 API는 이번 범위에 포함하지 않았습니다.

초대 토큰이 URL에 있으므로 Docker 기본 명령은 access log를 끕니다.
로컬 실행에서도 같은 옵션을 사용하고, 외부 프록시의 로그에도 토큰 원문을 남기지 않도록 설정합니다.

```sh
uv run uvicorn app.main:app --reload --no-access-log
```

## 검증

DB 테스트는 `TEST_DATABASE_URL`에 지정한 PostgreSQL 안에 고유 스키마를 생성·제거합니다.
운영 DB 대신 별도 테스트 DB를 지정합니다. 주소가 없으면 DB 테스트는 skip합니다.

```sh
uv sync --locked
uv run pytest -q
uv run ruff check app migrations tests
uv run ruff format --check app migrations tests
```

회원 가입·로그인, 가족 권한, 초대 만료·중복·취소, 동시 수락과 부모 수 제한을 검증합니다.
FID 등록·갱신·계정 전환·연결 해제와 내부 FCM 발송의 부분 실패도 검증합니다.
자동 테스트는 실제 FCM을 호출하지 않습니다.

## FCM FID와 푸시 발송

로그인한 앱은 `PUT /api/v1/users/me/push-registrations`에 Bearer JWT와 JSON을 보냅니다.
사용자당 여러 FID를 저장하며 기기 정보는 받지 않습니다. 같은 FID의 재등록은 멱등적입니다.

```json
{"fid": "current-fcm-fid", "previous_fid": "old-fcm-fid"}
```

`previous_fid`는 선택입니다. FID 변경 전 값을 아는 경우 함께 보내면 자기 이전 FID를 삭제합니다.
앱 시작·로그인 후와 FCM의 `onRegistered(installationId)` 콜백에서 등록된 FID를 서버에 보냅니다.
AndroidManifest에 다음 설정이 필요합니다.

```xml
<meta-data
    android:name="firebase_messaging_installation_id_enabled"
    android:value="true" />
```

자동 초기화가 꺼져 있으면 `FirebaseMessaging.getInstance().register()`로 FCM에 등록한 뒤
콜백의 FID를 사용합니다. FCM 등록 없이 Firebase Installations에서 ID만 조회한 상태와 구분합니다.
기존 FCM 등록 토큰을 `fid`에 넣어서는 안 됩니다. `token`·`previous_token` 요청 키는 지원하지 않습니다.
[Android 공식 FID 등록 안내](https://firebase.google.com/docs/cloud-messaging/android/get-started#access_the_firebase_installation_id)를 참고합니다.
입력은 원문을 보존하며 빈 값·공백뿐인 값·NUL(U+0000)·UTF-8 기준 2048바이트 초과 FID는 거부합니다.
`previous_fid`에도 같은 검증을 적용하며 오류 응답에 입력 원문을 포함하지 않습니다.

로그아웃·계정 전환 전에는 `DELETE /api/v1/users/me/push-registrations`에
`{"fid": "current-fcm-fid"}`을 보내 연결을 해제합니다.
두 API의 성공 응답은 본문 없는 204이며, 없는 FID의 해제도 204입니다.
JWT 만료만으로 푸시 연결이 지워지지는 않으므로 앱에서 연결 해제 실패를 처리해야 합니다.
같은 FID를 다른 계정에서 등록하면 새 계정으로 연결을 옮깁니다.
로그아웃 시에는 서버의 사용자 연결만 해제하며 Firebase installation 자체를 삭제할 필요는 없습니다.

발송은 공개 API 없이 `PushService(session_factory).send(PushSend(...))`로 내부에서 호출합니다.
제목·안내 문구와 사건 ID를 함께 보내며 무효 FID만 삭제합니다.
별도 큐·애플리케이션 자동 재시도·발송 복구는 없습니다.
현재 가족 확인·사건 분석 서비스는 이를 호출하지 않으며 기존 알림 요청도 자동 발송하지 않습니다.
향후 사건 저장 commit 후 응답 처리와 분리해 호출해야 합니다.

실제 발송에는 앱과 같은 Firebase 프로젝트의 서버 자격 증명이 필요합니다.
[Admin SDK 공식 설정](https://firebase.google.com/docs/admin/setup)에 따라
Application Default Credentials를 준비합니다.
비 Google 환경에서는 서버 프로세스의 `GOOGLE_APPLICATION_CREDENTIALS`에
저장소 밖 자격 증명 파일의 절대 경로를 지정할 수 있습니다.
Firebase 설정은 최초 발송 때 사용하며 FID API·일반 앱 기동에는 필수가 아닙니다.

Docker에서는 외부 키 파일을 컨테이너에 읽기 전용으로 마운트하고,
`GOOGLE_APPLICATION_CREDENTIALS`를 컨테이너 안 경로로 전달해야 합니다.
예를 들어 별도 Compose override에 다음을 설정합니다. 키 파일을 저장소나 이미지에 넣지 않습니다.

```yaml
services:
  api:
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /run/secrets/firebase-admin.json
    volumes:
      - /absolute/path/outside-repo/firebase-admin.json:/run/secrets/firebase-admin.json:ro
```

컨테이너의 `app` 사용자가 마운트된 파일을 읽을 수 있어야 합니다.
환경변수·마운트 변경 후에는 컨테이너를 재생성해야 합니다.
앱의 알림 권한·채널·수신 처리·선택 후 화면 이동은 Android 작업입니다.
FCM 접수 성공은 기기 수신이나 즉시 표시 성공을 보장하지 않습니다.
실제 기기 발송·수신은 자동 테스트와 별도로 검증해야 합니다.

전체 계약과 남은 연동은 [SPEC-004](docs/SPEC/SPEC-004.md)를 참고합니다.
