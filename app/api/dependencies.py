from typing import Annotated

from fastapi import Depends

from app.core.ai import AIClient
from app.core.database import SessionDep, session_factory
from app.services.analysis import AnalysisService
from app.services.event_query import EventQueryService
from app.services.event_status import EventStatusService
from app.services.family import FamilyService
from app.services.invitation import InvitationService
from app.services.push import PushService
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


def get_analysis_service(session: SessionDep) -> AnalysisService:
    """미설정 AI 클라이언트와 기존 알림 서비스를 주입합니다."""
    return AnalysisService(session, AIClient(), PushService(session_factory))


AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]


def get_event_query_service(session: SessionDep) -> EventQueryService:
    """자녀 사건 조회 서비스에 요청 세션을 주입합니다."""
    return EventQueryService(session)


EventQueryServiceDep = Annotated[EventQueryService, Depends(get_event_query_service)]


def get_event_status_service(session: SessionDep) -> EventStatusService:
    """사건 대응 상태 서비스에 요청 세션을 주입합니다."""
    return EventStatusService(session)


EventStatusServiceDep = Annotated[EventStatusService, Depends(get_event_status_service)]
