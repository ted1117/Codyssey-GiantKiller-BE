from typing import Annotated

from fastapi import Depends

from app.core.database import SessionDep
from app.services.family import FamilyService
from app.services.invitation import InvitationService


def get_family_service(session: SessionDep) -> FamilyService:
    """가족 서비스에 요청 세션을 주입합니다."""
    return FamilyService(session)


def get_invitation_service(session: SessionDep) -> InvitationService:
    """초대 서비스에 요청 세션을 주입합니다."""
    return InvitationService(session)


FamilyServiceDep = Annotated[FamilyService, Depends(get_family_service)]
InvitationServiceDep = Annotated[InvitationService, Depends(get_invitation_service)]
