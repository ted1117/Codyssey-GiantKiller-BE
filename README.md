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
기기·FCM 등록은 후속 작업입니다.
