#!/usr/bin/env bash
# cron이 매분 실행한다. origin/main이 바뀌었을 때만 deploy/deploy.sh를 실행한다.
# 사용법: auto-deploy.sh [--force]
set -euo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

exec 9>"${XDG_RUNTIME_DIR:-/tmp}/giantkiller-deploy.lock"
flock -n 9 || exit 0

STATE_FILE="${GIANTKILLER_DEPLOY_STATE:-$HOME/.giantkiller-deployed}"

git fetch --quiet --no-tags origin main
target="$(git rev-parse FETCH_HEAD)"
short="${target:0:7}"

last=""
if [[ -f "$STATE_FILE" ]]; then
  last="$(<"$STATE_FILE")"
fi
if [[ "$target" == "$last" && "${1:-}" != "--force" ]]; then
  exit 0
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  log "deploy_refused sha=$short reason=not_on_main branch=$branch"
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  log "deploy_refused sha=$short reason=tracked_changes"
  exit 1
fi
if ! git merge-base --is-ancestor HEAD "$target"; then
  log "deploy_refused sha=$short reason=diverged_or_ahead"
  exit 1
fi

# 깨진 커밋을 매분 재시도하지 않도록 배포 전에 시도한 커밋을 기록한다.
printf '%s\n' "$target" >"$STATE_FILE"

log "deploy_start sha=$short"
if git merge --ff-only --quiet "$target" && bash deploy/deploy.sh; then
  log "deploy_done sha=$short"
else
  log "deploy_failed sha=$short"
  exit 1
fi
