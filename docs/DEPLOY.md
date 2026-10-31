# Ncloud 배포

Ncloud Linux 서버 한 대에서 기존 Docker Compose의 API와 PostgreSQL 18을 실행한다.
서버가 1분마다 GitHub `main`을 확인(cron)해 바뀌면 자동으로 배포한다.
`chore/ncloud-deploy` 등 다른 브랜치는 배포하지 않는다. 클라우드 리소스 생성·HTTPS·자동 롤백은 포함하지 않는다.

## 1. 네트워크 구성

서버는 사설 IP(`10.19.235.127`, SSH 포트 2223)만 가지며 팀 VPN으로만 접속할 수 있다.
GitHub 호스팅 runner는 이 서버에 접속할 수 없으므로 GitHub Actions SSH 배포는 사용하지 않는다.
대신 서버가 GitHub를 주기적으로 조회(polling)한다. 서버에는 바깥으로 나가는 HTTPS만 필요하다.
PostgreSQL의 5432 포트는 외부에 개방하지 않는다.

## 2. 서버 최초 준비

서버에 Git, Bash, `flock`, Docker Engine과 `docker compose` 플러그인이 필요하다.
Compose는 `up --wait --wait-timeout`을 지원해야 한다.
배포 사용자(`ncloud`)는 비대화형 `docker` 실행 권한(docker 그룹)이 필요하다.
Docker 그룹 권한은 호스트의 root 수준 권한이므로 전용 배포 계정을 사용한다.

저장소는 공개이므로 배포 키 없이 HTTPS로 clone한다. 서버에는 이미 아래 경로에 `main` clone이 있다.

```sh
git clone --branch main https://github.com/ted1117/Codyssey-GiantKiller-BE.git /home1/ncloud/Codyssey-GiantKiller-BE
cd /home1/ncloud/Codyssey-GiantKiller-BE
```

서버 저장소는 `main`과 깨끗한 추적 파일 상태를 유지한다. 스크립트는 강제 reset하지 않고
다른 브랜치·추적 파일 변경·배포 대상보다 앞서거나 갈라진 커밋이 있으면 중단한다.
추적되지 않는 파일(에디터 임시 파일 등)은 배포를 막지 않는다. 서버에서 추적 파일을 수정하지 않는다.

## 3. 서버 환경 설정

서버에서만 `/home1/ncloud/Codyssey-GiantKiller-BE/.env`를 만들고 권한을 `600`으로 제한한다.
아래는 필드 안내이며 비밀번호와 인증 키는 새로 생성한다. 로컬 `.env`를 복사할 필요는 없다.

```dotenv
POSTGRES_DB=giantkiller
POSTGRES_USER=giantkiller
POSTGRES_PASSWORD=<새로운 강력한 DB 비밀번호>
AUTH_SECRET=<최소 32자 무작위 문자열>
API_PORT=8000
AUTH_TOKEN_LIFETIME_SECONDS=3600
# 선택: AI 분석 API 키. 없으면 분석 API가 503 AI_NOT_CONFIGURED를 반환한다.
AI_API_KEY=<AI API 키>
```

선택 설정 `INVITATION_BASE_URL`은 앱 초대 화면 URL이다.
`AI_API_KEY`가 없으면 통화·문자 분석 API가 `503 AI_NOT_CONFIGURED`를 반환한다.
음성 인식(whisper) 모델(약 480MB)은 첫 기동 때 한 번 내려받아 `stt_cache` 볼륨에 저장하므로 첫 기동은 오래 걸릴 수 있다.
Compose 환경변수 값에 `$` 등이 포함되면 Compose의 `.env` 인용 규칙에 맞게 작성한다.
DB 비밀번호를 바꿔도 기존 PostgreSQL 볼륨의 사용자 비밀번호는 자동 변경되지 않는다.

FCM 발송이 필요하면 키를 저장소 밖에 두고 `/etc/giantkiller/compose.override.yaml`을 만든다.
이 파일이 있으면 배포 스크립트가 추가로 적용한다. 다음 경로는 서버에 준비한 실제 경로로 바꾼다.

```yaml
services:
  api:
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /run/secrets/firebase-admin.json
    volumes:
      - /etc/giantkiller/firebase-admin.json:/run/secrets/firebase-admin.json:ro
```

이미지의 `app` 사용자가 키 파일을 읽을 수 있도록 파일 소유권·권한을 설정한다.
Firebase 키를 저장소·이미지·배포 로그에 넣지 않는다.

## 4. 최초 전환과 cron 등록

이 브랜치(`chore/ncloud-deploy`)를 먼저 `main`에 머지해야 한다. 서버 clone의 `main`에는 아직
`deploy/` 디렉터리가 없으므로, 머지 후 서버에서 `git pull --ff-only origin main`으로 받아야
`deploy/auto-deploy.sh`가 생긴다.

그다음 `.env`를 만들고(권한 600) 기존에 수동으로 띄운 스택을 내린다. 기존 프로젝트 이름은
`codyssey-giantkiller-be`이며 DB에 테이블이 없었으므로 데이터 이전은 필요 없다.
`docker compose ... down`은 `compose.yaml`과 `.env`를 읽으므로 반드시 저장소 디렉터리에서 실행한다.
**`-v`를 붙이지 않는다.**

```sh
cd /home1/ncloud/Codyssey-GiantKiller-BE
git pull --ff-only origin main
chmod 600 .env
docker compose -p codyssey-giantkiller-be down
bash deploy/auto-deploy.sh --force
```

첫 배포가 성공하면 `crontab -e`에 다음 한 줄을 추가한다.

```cron
* * * * * /home1/ncloud/Codyssey-GiantKiller-BE/deploy/auto-deploy.sh >> /home1/ncloud/giantkiller-deploy.log 2>&1
```

## 5. 운영과 확인

- 배포는 `main`에 머지하면 된다. 약 1분 + 빌드 시간 안에 반영된다.
- 로그 확인: `tail -f ~/giantkiller-deploy.log` (`deploy_start`, `deploy_done`, `deploy_failed`, `deploy_refused` 줄).
- 수동 재배포: `/home1/ncloud/Codyssey-GiantKiller-BE/deploy/auto-deploy.sh --force`
- 자동 배포 중지: `crontab -e`에서 해당 줄을 주석 처리한다.
- 실패한 커밋은 새 커밋이 올라오거나 `--force`를 쓰기 전에는 다시 시도하지 않는다.
  마지막으로 시도한 커밋은 `~/.giantkiller-deployed`에 기록한다.
- 같은 시각에는 한 번에 하나만 실행한다(`flock`). 로그 로테이션은 설정하지 않았으므로 파일이 커지면 직접 정리한다.

배포 순서(`deploy/deploy.sh`)는 Compose 설정 검증 → 서버에서 API 이미지 빌드 → DB 기동 확인 →
`alembic upgrade head` → API 재생성 → `/health` 상태 확인이다.
API 기동 확인 제한은 180초, DB는 120초다.
[Compose 기동 대기](https://docs.docker.com/reference/cli/docker/compose/up/)를 사용한다.

배포 후 서버에서 확인한다.

```sh
curl --fail http://127.0.0.1:8000/health
```

VPN 접속 상태에서 확인 주소는 `http://10.19.235.127:8000/health`, Swagger는 `/docs`다.
현재 API 포트는 HTTP이므로 **실제 사용자 비밀번호·JWT를 보내기 전에 HTTPS 프록시나
TLS를 종료하는 로드 밸런서를 설정**한다. 프록시 access log에는 초대 토큰 URL을 남기지 않는다.
`/health`는 HTTP 응답 확인이며 전체 기능·FCM·AI 연동을 검증하지 않는다.

## 실패와 데이터 유지

빌드·마이그레이션 실패 시 API 교체 단계로 진행하지 않는다. 기존 API는 최초 배포를 제외하면
그대로 실행되지만 DB 마이그레이션은 기존 API에도 영향을 줄 수 있다.
API 재기동 실패 시 자동 롤백하지 않는다. `giantkiller-deploy.log`와 서버 상태를 확인한다.
기존 API를 재생성하는 동안 짧은 중단이 발생할 수 있다.

배포 전 DB 백업을 준비한다. 스크립트는 프로젝트 이름을 `giantkiller`로 고정하고
`giantkiller_postgres_data` 볼륨을 유지한다. `docker compose down -v`는 데이터를 삭제한다.
기존에 다른 Compose 프로젝트 이름으로 운영하던 DB가 있으면 이 구성은 별도 볼륨을 만들므로
먼저 실제 볼륨과 이전 계획을 확인한다. 운영 DB를 자동으로 이전하지 않는다.
