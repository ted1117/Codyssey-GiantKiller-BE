from typing import Annotated

from fastapi import Depends

from app.core.database import SessionDep
from app.services.family import FamilyService
from app.services.invitation import InvitationService
from app.services.push_registration import PushRegistrationService
from app.services.verification import VerificationService


def get_family_service(session: SessionDep) -> FamilyService:
    """가족 서비스에 요청 세션을 주입합니다."""
    return FamilyService(session)


def get_invitation_service(session: SessionDep) -> InvitationService:
    """초대 서비스에 요청 세션을 주입합니다."""
    return InvitationService(session)


def get_verification_service(session: SessionDep) -> VerificationService:
    """가족 확인 서비스에 요청 세션을 주입합니다."""
    return VerificationService(session)


def get_push_registration_service(session: SessionDep) -> PushRegistrationService:
    """토큰 서비스에 인증과 동일한 요청 세션을 주입합니다."""
    return PushRegistrationService(session)


FamilyServiceDep = Annotated[FamilyService, Depends(get_family_service)]
InvitationServiceDep = Annotated[InvitationService, Depends(get_invitation_service)]
PushRegistrationServiceDep = Annotated[
    PushRegistrationService, Depends(get_push_registration_service)
]
VerificationServiceDep = Annotated[
    VerificationService, Depends(get_verification_service)
]
