from typing import Annotated

from fastapi import Depends

from app.core.ai import AIClient
from app.core.config import SettingsDep
from app.core.database import SessionDep, session_factory
from app.services.analysis import AnalysisService
from app.services.app_install import AppInstallService
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


def get_push_service() -> PushService:
    """요청 세션과 분리된 세션 팩토리로 알림 서비스를 만듭니다."""
    return PushService(session_factory)


PushServiceDep = Annotated[PushService, Depends(get_push_service)]


def get_verification_service(
    session: SessionDep, push: PushServiceDep
) -> VerificationService:
    """가족 확인 서비스에 요청 세션과 알림 서비스를 주입합니다."""
    return VerificationService(session, push)


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


def get_ai_client(settings: SettingsDep) -> AIClient:
    """설정에서 AI 클라이언트를 만듭니다. 키가 없으면 미설정 상태입니다."""
    key = settings.ai_api_key
    return AIClient(
        key.get_secret_value() if key else None,
        base_url=str(settings.ai_base_url),
        model=settings.ai_model,
        timeout=settings.ai_timeout_seconds,
        stt_model=settings.stt_model,
        stt_device=settings.stt_device,
        stt_compute_type=settings.stt_compute_type,
    )


AIClientDep = Annotated[AIClient, Depends(get_ai_client)]


def get_analysis_service(
    session: SessionDep, push: PushServiceDep, ai: AIClientDep
) -> AnalysisService:
    """AI 클라이언트와 기존 알림 서비스를 주입합니다."""
    return AnalysisService(session, ai, push)


AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]


def get_event_query_service(session: SessionDep) -> EventQueryService:
    """자녀 사건 조회 서비스에 요청 세션을 주입합니다."""
    return EventQueryService(session)


EventQueryServiceDep = Annotated[EventQueryService, Depends(get_event_query_service)]


def get_event_status_service(session: SessionDep) -> EventStatusService:
    """사건 대응 상태 서비스에 요청 세션을 주입합니다."""
    return EventStatusService(session)


EventStatusServiceDep = Annotated[EventStatusService, Depends(get_event_status_service)]


def get_app_install_service(
    session: SessionDep, push: PushServiceDep
) -> AppInstallService:
    """앱 설치 서비스에 요청 세션과 알림 서비스를 주입합니다."""
    return AppInstallService(session, push)


AppInstallServiceDep = Annotated[AppInstallService, Depends(get_app_install_service)]
