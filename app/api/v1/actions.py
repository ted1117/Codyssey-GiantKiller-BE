from fastapi import APIRouter

from app.api.dependencies import AppInstallServiceDep
from app.core.auth import CurrentUser
from app.schemas.action import AppInstallCreate, AppInstallRead

router = APIRouter(prefix="/actions", tags=["actions"])


@router.post("/app-install", response_model=AppInstallRead, status_code=200)
async def report_app_install(
    body: AppInstallCreate, user: CurrentUser, service: AppInstallServiceDep
) -> AppInstallRead:
    """부모 기기의 앱 설치를 판정하고 위험 설치만 사건으로 저장합니다."""
    return await service.report(user.id, body)
