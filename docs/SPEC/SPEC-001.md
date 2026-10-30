# SPEC-001: 회원·가족·초대

- 관련 요구사항: [PRD-001](../PRD/PRD-001.md)
- 대상 브랜치: `feature/user`
- 상태: 이번 브랜치 범위 구현·검증 완료
- 구현 상태: 모델·레포지토리·API 스키마·인증·서비스·엔드포인트 완료

## 1. 목적과 범위

부모님과 자녀가 각자 가입한 뒤, 대표 자녀가 공유한 초대 링크로 가족을 연결한다.
부모님과 자녀 모두 Android 앱을 사용하며, 자녀는 웹에서도 사건을 조회한다.

이번 브랜치에 포함한다.

- FastAPI Users 기반 이메일·비밀번호·이름 가입, 로그인, 내 정보 조회
- 가족 생성, 내가 속한 가족 목록, 가족·멤버 조회
- 대표 자녀의 초대 링크 생성·취소와 초대받은 사람의 수락
- 가족 소속과 대표 권한 검사

이번 브랜치에서 제외한다.

- 기기·FCM 토큰 등록과 푸시 발송
- 서버의 카카오톡·문자·메일 초대 발송
- 사칭 확인 요청의 대상 선정과 여러 자녀의 응답 판정, PRD-003에서 다룬다.

PRD-001의 전체 범위에는 기기·FCM 등록이 남아 있으나, 이번 브랜치에서는 후속 작업으로 미룬다.
이 문서는 PRD-001을 수정하지 않고 현재 브랜치의 합의와 설계안을 기록한다.

## 2. 확정된 정책

### 2.1. 계정과 가족 역할

- 가입 정보는 이메일·비밀번호·이름이다.
- 부모·자녀 역할은 계정 전체가 아니라 가족 소속 관계에 저장한다.
- 역할 값은 `PARENT`, `CHILD`다. 아버지·어머니·아들·딸·사위를 별도 권한으로 구분하지 않는다.
- 한 계정이 여러 가족에 참여할 수 있다. 자녀가 친가와 처가에 각각 참여할 수 있다.
- 가족당 부모는 최대 2명이며, 자녀 수는 별도로 제한하지 않는다.
- 부모가 아직 참여하지 않은 가족도 생성할 수 있다.

### 2.2. 대표 멤버

- 가족을 생성한 사용자를 `CHILD`이자 해당 가족의 대표로 등록한다.
- 가족당 대표는 1명이며, 대표는 자녀여야 한다.
- 대표 자녀가 부모와 다른 자녀를 초대한다.
- 초대 생성과 취소는 해당 가족의 대표만 할 수 있다.
- 대표 여부와 `PARENT/CHILD` 역할은 별도 정보다.

### 2.3. 초대 링크

- 대표가 초대 생성 시 참여 역할 `PARENT/CHILD`를 지정한다.
- 대표는 생성된 링크를 카카오톡 등의 공유 기능으로 직접 전달한다.
- 초대는 생성 후 24시간 동안 유효하며, 1회만 사용할 수 있다.
- 대표가 사용 전 초대를 취소할 수 있다.
- 초대받은 사람은 가입 또는 로그인 후 가족과 참여 역할을 확인하고 수락한다.
- 수락 시 로그인한 사용자를 초대에 지정된 가족과 역할로 연결한다.
- 기존 계정도 링크로 다른 가족에 참여할 수 있다.
- 만료·취소·사용 완료된 초대로는 참여할 수 없다.
- 같은 가족에 이미 참여한 사용자의 중복 참여는 거부한다.
- 초대 링크 공유 화면과 링크를 앱으로 여는 처리는 클라이언트 작업이다.

### 2.4. 이번 구현의 기본 동작

- Bearer JWT를 사용한다. 기본 유효 시간은 3600초이며 환경변수로 조정한다.
- JWT 서명 비밀 `AUTH_SECRET`은 필수이며 최소 32자다. 기본 비밀 값은 제공하지 않는다.
- 토큰 갱신·서버 측 폐기·로그아웃 엔드포인트는 추가하지 않는다. 만료 후 다시 로그인한다.
- 이메일은 소문자로 저장하며, FastAPI Users의 기본 Argon2 비밀번호 해시를 사용한다.
- 이메일 인증 여부와 무관하게 활성 계정은 로그인할 수 있다. 클라이언트가 가입 시 지정한 권한 플래그는 무시한다.
- 계정 전체의 역할을 고정하지 않는다. 가족에 따라 부모·자녀로 참여할 수 있다.
- 가족 미가입 계정도 로그인할 수 있으며 가족 목록은 빈 배열이다.
- 초대 생성과 수락 시 부모 최대 2명을 검사한다. 부모가 2명이더라도 자녀 초대는 가능하다.
- 이미 취소한 초대의 재취소는 204로 처리한다. 사용 완료 초대 취소는 409, 만료 초대 취소는 410이다.
- 만료 판정은 `expires_at <= 현재 시각`이다. 수락 시 잠금을 획득한 뒤 다시 검사한다.
- `INVITATION_BASE_URL`이 있으면 해당 prefix에 토큰을 붙인다.
  미설정 시 요청 서버의 API 미리보기 URL을 반환한다. 클라이언트 연결 화면은 제공하지 않는다.

기존 PRD의 열린 질문 중 링크 방식·24시간·1회 사용과 대표 초대 권한은 채팅에서 확정했다.
역할 겸용 등 나머지 기본 동작은 이번 구현 선택이며, 제품 정책이 바뀌면 조정한다.

## 3. 엔드포인트

공통 prefix는 `/api/v1`이다.
경로와 기능은 합의됐으며, 아래 응답 스키마는 5장의 구현을 따른다.

### 3.1. 회원·인증

- `POST /auth/register`: 이메일·비밀번호·이름으로 회원가입
- `POST /auth/login`: 로그인
- `GET /users/me`: 로그인한 사용자 정보 조회

가입·로그인은 인증 없이 호출한다. 내 정보 조회에는 로그인이 필요하다.
로그인은 FastAPI Users 기본 form 형식을 사용한다.
`username` 필드에 이메일을, `password` 필드에 비밀번호를 전달한다.
부모·자녀 역할은 가입 요청에 넣지 않는다.

### 3.2. 가족

- `POST /families`: 가족 생성, 생성자를 대표 자녀로 등록
- `GET /families`: 내가 속한 가족 목록 조회
- `GET /families/{family_id}`: 가족 정보와 멤버 조회

모두 로그인이 필요하다. 가족 상세는 해당 가족 멤버만 조회할 수 있다.

### 3.3. 초대 관리

- `POST /families/{family_id}/invitations`: 역할을 지정해 초대 링크 생성
- `DELETE /families/{family_id}/invitations/{invitation_id}`: 초대 취소

해당 가족의 대표 자녀만 호출할 수 있다.
취소 시 경로의 가족과 초대의 가족이 일치하는지도 확인한다.

### 3.4. 초대받은 사람

- `GET /invitations/{token}`: 수락 전 가족·참여 역할 확인
- `POST /invitations/{token}/accept`: 초대 수락

둘 다 로그인이 필요하다. 초대 토큰만으로 로그인한 것으로 취급하지 않는다.

## 4. DB 모델

아래 컬럼·타입·제약은 1단계에서 구현했다.
PostgreSQL을 유지하며, ID는 UUID, 시각은 타임존이 있는 UTC 기준이다.
모델의 공통 Base는 DB 연결 없이 메타데이터를 사용할 수 있도록 `app/models/base.py`에 둔다.

### 4.1. User

FastAPI Users의 UUID 사용자 모델을 상속하고 이름을 추가한다.

```text
id                 UUID PK
email              이메일, UNIQUE
hashed_password    비밀번호 해시
is_active          활성 계정 여부
is_superuser       관리자 여부
is_verified        이메일 인증 여부
name               이름, VARCHAR(100)
```

`is_superuser`는 FastAPI Users의 관리자 속성이며, 가족 대표 권한으로 사용하지 않는다.
`role`은 User에 두지 않는다.

### 4.2. Family

```text
id                 UUID PK
created_at         생성 시각
```

### 4.3. FamilyMember

```text
id                   UUID PK
family_id            FK → Family.id
user_id              FK → User.id
role                 ENUM: PARENT / CHILD
is_representative    대표 여부, 기본 false
joined_at            참여 시각
```

적용한 DB 제약은 다음과 같다.

- `UNIQUE(family_id, user_id)`: 같은 가족 중복 가입 방지
- 대표 행에 대한 가족별 UNIQUE 제약: 가족당 대표가 최대 1명
- 대표 행의 역할은 `CHILD`여야 한다는 CHECK 제약

레포지토리는 가족과 멤버를 각각 저장하며 commit하지 않는다.
가족 서비스는 가족 생성과 대표 멤버 등록을 하나의 트랜잭션으로 묶는다.
초대 서비스는 가족 행 잠금 후 역할별 멤버 수를 조회해 부모 최대 2명을 검사한다.

### 4.4. FamilyInvitation

```text
id                      UUID PK
family_id               FK → Family.id
created_by_member_id    FK → FamilyMember.id
role                    ENUM: PARENT / CHILD
token_hash              초대 토큰 해시, VARCHAR(64), UNIQUE
created_at              생성 시각
expires_at              생성 후 24시간
accepted_at             수락 시각, nullable
accepted_by_user_id     FK → User.id, nullable
revoked_at              취소 시각, nullable
```

- 추측하기 어려운 초대 토큰을 생성하고, 원문은 링크 생성 응답에만 제공한다.
- DB에는 토큰 원문 대신 해시를 저장한다.
- 별도 `status` 컬럼 없이 만료·수락·취소 시각으로 사용 가능 여부를 판단한다.
- 수락은 멤버 생성과 초대 사용 처리를 하나의 트랜잭션으로 묶는다.
- 동시 수락과 취소가 겹쳐도 하나의 결과만 확정되도록 처리한다.
- 수락 거부 시 초대를 사용 완료로 바꾸거나 멤버를 일부 생성하지 않는다.

레포지토리는 토큰 해시·만료 시각을 받아 저장하고, 초대를 조회·잠금·갱신한다.
초대 서비스는 `secrets.token_urlsafe(32)`로 생성한 토큰의 SHA-256 해시만 저장한다.
서비스에서 24시간 계산, 대표 권한·만료·사용 여부 판단과 트랜잭션을 연결했다.
DB에는 수락자·수락 시각의 동시 기록과 수락·취소의
상호 배타 CHECK 제약을 적용했다.

### 4.5. 레포지토리와 마이그레이션

- `UserRepository`: FastAPI Users의 SQLAlchemy 어댑터를 재사용한다.
- `FamilyRepository`: 가족 생성, ID 조회·행 잠금, 사용자별 가족 목록 조회
- `FamilyMemberRepository`: 멤버 생성, 가족·사용자 조회, 멤버·대표 조회, 역할별 수 조회
- `FamilyInvitationRepository`: 초대 저장, 가족·ID 또는 해시로 조회·행 잠금, 수락·취소 기록
- 가족·멤버·초대 레포지토리는 flush만 수행하며 commit·rollback은 호출자가 담당한다.
- FastAPI Users의 계정 변경 메서드는 내부에서 commit한다. 가족 트랜잭션과 분리해서 사용한다.
- 초기 마이그레이션: `migrations/versions/0001_membership.py`
- 적용 명령: `uv run alembic upgrade head`. 대상 PostgreSQL 연결 설정을 확인한 후 실행한다.

검증에는 분리된 PostgreSQL 18만 사용했으며, 기존 프로젝트 DB에는 적용하지 않았다.

### 4.6. 서비스 트랜잭션

- 인증 조회와 가족 작업은 요청별 동일 세션을 사용한다.
- 서비스의 쓰기 트랜잭션은 인증 조회로 시작된 트랜잭션까지 포함한다.
- 성공 시 한 번 commit하며, 예외·취소 시 rollback한다.
- 초대 수락·취소는 가족 행 → 초대 행 순서로 잠근다.
- 잠금 후 최신 초대 상태를 다시 읽어 만료·수락·취소 여부를 검사한다.
- 초대 수락은 멤버 등록과 수락자·수락 시각 저장을 함께 확정한다.

## 5. API 스키마

2단계에서 구현한 요청·응답 스키마를 3단계 엔드포인트와 서비스에 연결했다.

### 5.1. 요청

```text
UserCreate
  email
  password
  name

InvitationCreate
  role: PARENT / CHILD
```

- 로그인은 form 요청이므로 별도 JSON 로그인 스키마를 만들지 않는다.
- 가족 생성은 본문 없이 호출한다. 사용자 ID·역할·대표 여부는 서버가 지정한다.
- 초대 수락은 본문 없이 로그인 사용자와 경로의 토큰으로 처리한다.
- 클라이언트가 수락 시 가족 ID나 참여 역할을 바꿀 수 없도록 한다.
- 가입 이름은 앞뒤 공백 제거 후 1~100자이며, 이메일 형식을 검증한다.
- 비밀번호는 빈 문자열을 거부하고 공백을 임의로 제거하지 않는다. 객체 repr에서는 숨긴다.
- 가입·초대 요청은 정의되지 않은 필드를 거부한다. 초대 요청으로 만료 시각이나 대표 여부를 지정할 수 없다.
- `UserCreate`는 FastAPI Users의 `BaseUserCreate`를 상속하므로 라이브러리 기본 `is_*` 필드도 존재한다.
  일반 가입용 `create_update_dict()`는 해당 필드를 제외한다. 인증 연결 시 일반 가입의 safe 처리를 유지한다.

### 5.2. 응답

```text
UserRead
  id
  email
  name
  is_active
  is_superuser
  is_verified

FamilyRead
  id
  created_at

FamilyMemberRead
  id
  user_id
  name
  role
  is_representative
  joined_at

FamilyDetail
  FamilyRead 필드
  members: list[FamilyMemberRead]

InvitationCreated
  id
  family_id
  role
  invite_url
  expires_at

InvitationPreview
  family_id
  representative_name
  role
  expires_at
```

- `UserRead`, `FamilyRead`, `FamilyMemberRead`는 ORM 속성에서 변환할 수 있다.
- `FamilyMemberRead.name`은 연결된 User에서 가져온다. 이름을 멤버 테이블에 중복 저장하지 않는다.
  ORM 변환 전 레포지토리에서 `user` 관계를 명시적으로 로드해야 한다. 평면 `name` 입력도 지원한다.
- 가족 생성·상세 응답은 `FamilyDetail`, 가족 목록은 `list[FamilyRead]`로 구성한다.
  `FamilyDetail.members`는 서비스에서 조회 결과를 조합해 명시적으로 전달한다.
- 초대 수락 응답은 생성된 `FamilyMemberRead`를 사용한다.
- 응답 시각에는 타임존이 필수이며, 공유 링크는 URL 형식으로 검증한다.
- 비밀번호 해시와 초대 토큰 해시는 응답에 포함하지 않는다.
- 비밀번호·인증 토큰·초대 토큰 원문을 로그에 남기지 않는다.

## 6. 오류·운영 설정과 후속 범위

서비스 오류는 `{"detail": "오류 코드"}`로 반환한다.

- 403: `FAMILY_ACCESS_DENIED`, `REPRESENTATIVE_REQUIRED`
- 404: `FAMILY_NOT_FOUND`, `INVITATION_NOT_FOUND`
- 409: `INVITATION_USED`, `ALREADY_FAMILY_MEMBER`, `PARENT_LIMIT_REACHED`
- 410: `INVITATION_EXPIRED`, `INVITATION_REVOKED`

FastAPI Users의 중복 가입·로그인 실패는 라이브러리 기본 400 응답이다.
인증 없음·유효하지 않은 토큰·만료 토큰·비활성 계정은 보호된 API에서 401이다.
입력 검증 실패는 422다. 일반 가입의 권한 조작과 동시 중복 가입을 검증했다.

실행에는 `AUTH_SECRET` 설정과 명시적인 Alembic 마이그레이션이 필요하다.
Docker 이미지에 마이그레이션을 포함했으며, API 시작 시 자동 적용하지 않는다.
인증 실패·권한 거부는 상태·고정 오류 코드만 기록한다.
초대 토큰이 URL에 있으므로 Docker 명령과 로컬 실행 안내에서 Uvicorn access log를 끈다.
외부 프록시 로그의 토큰 처리와 실제 앱·웹 초대 화면 주소는 배포·클라이언트 작업이다.

이번 범위 밖에 남겨둔 기능은 다음과 같다.

- 추가 비밀번호 정책과 이메일 인증·비밀번호 재설정 흐름
- 대표 변경·가족 탈퇴·멤버 제거
- 앱의 가족 미가입 화면과 초대 링크 연결 화면
- JWT 갱신·서버 측 폐기
- 기기·FCM 등록과 알림, 사칭 확인 응답 정책

의존성 확인 시 FastAPI Users SQLAlchemy 어댑터 7.0.0의 SQLAlchemy 요구 범위는
`>=2.0.0,<2.1.0`이었으며, 기존 프로젝트의 `sqlalchemy[asyncio]>=2.1.6`과 충돌했다.
사용자 승인에 따라 프로젝트 범위를 `sqlalchemy[asyncio]>=2.0,<2.1`로 변경하고,
잠금 파일과 로컬 환경을 최신 안정 2.0 버전인 `2.0.55`로 맞췄다.
1단계에서 `fastapi-users[sqlalchemy]` 15.0.5와 어댑터 7.0.0을 설치했다.
3단계에서 인증 설정·서비스·엔드포인트를 연결했다.

## 7. 검증 계획

- 가입 → 로그인 → 내 정보 조회, 중복 이메일 가입 거부
- 가족 생성 시 생성자가 대표 `CHILD`로 등록되는지 확인
- 대표의 초대 생성·취소와 일반 멤버·타 가족 사용자의 호출 거부
- 초대 역할에 따른 멤버 등록, 기존 계정의 다른 가족 참여
- 만료 시각 전후, 취소·사용 완료 초대, 같은 가족 중복 참여 거부
- 초대 동시 수락 시 멤버 1명만 등록되고 초대도 한 번만 사용되는지 확인
- 수락과 취소의 동시 요청에서 결과가 모순되지 않는지 확인
- 부모가 2명인 가족의 추가 부모 참여 거부, 동시 수락에서도 제한 유지
- 타 가족의 정보·멤버 조회 차단

FCM 발송과 사칭 확인 응답 판정은 이 브랜치의 검증 범위에서 제외한다.

1단계 검증 결과: PostgreSQL 18 통합 테스트 15개 통과.
초기 마이그레이션과 모델의 차이 없음, downgrade·upgrade 왕복,
DB 제약·가족별 조회·다중 가족 참여·트랜잭션 롤백·행 잠금과 재조회 갱신을 확인했다.
서비스·인증·API 정책 검증은 3단계에서 추가했다.

2단계 검증 결과: 스키마 검증 19개를 추가해 PostgreSQL 18 기준 전체 34개 통과.
가입 입력 검증, 일반 가입용 dict의 권한 필드 제외, 응답의 비밀 값 제외,
초대 요청의 추가 필드 거부, 시각의 타임존, 실제 ORM 멤버의 응답 변환을 확인했다.

3단계 검증 결과: PostgreSQL 18 기준 전체 49개 통과.
실제 라우터를 통해 가입·로그인·JWT 만료·비활성 계정, 가족 생성·조회·권한,
초대 24시간·취소·중복·다중 가족 참여, 동시 중복 가입·수락·부모 수 제한,
수락·취소 경합과 부분 쓰기 롤백을 확인했다.
Docker 이미지 빌드·이미지 내 마이그레이션·앱 시작과 가입·로그인·가족·초대 실행도 통과했다.
