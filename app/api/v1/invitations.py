from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import InvitationServiceDep
from app.core.auth import CurrentUser
from app.core.config import SettingsDep
from app.schemas.family import FamilyMemberRead
from app.schemas.invitation import (
    InvitationCreate,
    InvitationCreated,
    InvitationPreview,
)

router = APIRouter(tags=["invitations"])


@router.post(
    "/families/{family_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    family_id: UUID,
    body: InvitationCreate,
    request: Request,
    user: CurrentUser,
    settings: SettingsDep,
    service: InvitationServiceDep,
) -> InvitationCreated:
    """가족 대표가 역할을 지정해 공유용 초대 링크를 만듭니다."""
    base_url = (
        str(settings.invitation_base_url)
        if settings.invitation_base_url is not None
        else f"{str(request.base_url).rstrip('/')}/api/v1/invitations"
    )
    return await service.create(family_id, user.id, body.role, base_url)


@router.delete(
    "/families/{family_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    family_id: UUID,
    invitation_id: UUID,
    user: CurrentUser,
    service: InvitationServiceDep,
) -> Response:
    """가족 대표가 미사용 초대를 취소합니다."""
    await service.revoke(family_id, invitation_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invitations/{token}", response_model=InvitationPreview)
async def preview_invitation(
    token: str, user: CurrentUser, service: InvitationServiceDep
) -> InvitationPreview:
    """로그인한 사용자가 수락 전에 가족과 참여 역할을 확인합니다."""
    return await service.preview(token)


@router.post("/invitations/{token}/accept", response_model=FamilyMemberRead)
async def accept_invitation(
    token: str, user: CurrentUser, service: InvitationServiceDep
) -> FamilyMemberRead:
    """로그인한 사용자가 초대를 수락해 가족에 참여합니다."""
    return await service.accept(token, user.id)
