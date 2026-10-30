from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import VerificationServiceDep
from app.core.auth import CurrentUser
from app.schemas.verification import VerificationRead, VerificationRespond

router = APIRouter(prefix="/verify", tags=["verify"])


@router.post("/respond", response_model=VerificationRead, status_code=200)
async def respond(
    body: VerificationRespond, user: CurrentUser, service: VerificationServiceDep
) -> VerificationRead:
    """로그인한 대상 자녀의 발송 여부를 기록합니다."""
    return await service.respond(body.verification_id, user.id, body.response)


@router.get("/{verification_id}", response_model=VerificationRead, status_code=200)
async def get_verification(
    verification_id: UUID, user: CurrentUser, service: VerificationServiceDep
) -> VerificationRead:
    """해당 가족 멤버에게 확인 상태와 개인 응답을 반환합니다."""
    return await service.get_detail(verification_id, user.id)
