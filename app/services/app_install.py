import logging
from datetime import UTC, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.event import AnalysisStatus
from app.models.family import FamilyRole
from app.repositories.event import SecurityEventRepository
from app.repositories.family import FamilyMemberRepository
from app.schemas.action import AppInstallCreate, AppInstallRead
from app.schemas.push import PushMessageType, PushSend
from app.services.family import FamilyService
from app.services.push import PushService
from app.services.transaction import transaction

logger = logging.getLogger(__name__)
REMOTE_PACKAGE_PREFIXES = ("com.teamviewer.", "com.anydesk.", "com.rsupport.")
RELATED_CALL_WINDOW = timedelta(hours=24)


class AppInstallService:
    """앱 설치를 판정하고 위험 설치만 저장한 뒤 자녀에게 알립니다."""

    def __init__(self, session: AsyncSession, push: PushService) -> None:
        """저장 세션과 독립 발송 서비스를 주입받습니다."""
        self.session = session
        self.push = push
        self.events = SecurityEventRepository(session)
        self.members = FamilyMemberRepository(session)

    async def report(self, user_id: UUID, body: AppInstallCreate) -> AppInstallRead:
        """부모의 앱 설치를 판정하고 위험하면 사건 저장 후 자녀에게 알립니다."""
        async with transaction(self.session):
            member = await FamilyService(self.session).require_member(
                body.family_id, user_id
            )
            if member.role != FamilyRole.PARENT:
                raise ServiceError(403, "PARENT_REQUIRED")
            matched = (
                body.package_name.startswith(REMOTE_PACKAGE_PREFIXES)
                or body.is_unknown_source
            )
            if not matched:
                return AppInstallRead(matched=False, event_id=None, related_call=False)
            occurred_at = body.occurred_at.astimezone(UTC)
            related_call = await self.events.has_attention_call(
                body.family_id, occurred_at - RELATED_CALL_WINDOW, occurred_at
            )
            label = body.app_label
            summary = (
                f"최근 의심 통화 뒤 '{label}' 앱 설치가 감지됐어요."
                if related_call
                else f"위험할 수 있는 '{label}' 앱 설치가 감지됐어요."
            )
            event = await self.events.create(
                body.family_id,
                member.id,
                "APP_INSTALL",
                "RISKY_APP_INSTALL",
                occurred_at,
                risk_level="HIGH",
                summary=summary,
                requires_attention=True,
                analysis_status=AnalysisStatus.COMPLETED,
            )
            event_id = event.id
            targets = [
                m.user_id
                for m in await self.members.list_by_family(body.family_id)
                if m.role == FamilyRole.CHILD
            ]
        logger.info(
            "app_install_matched event_id=%s related_call=%s", event_id, related_call
        )
        for target in targets:
            try:
                await self.push.send(
                    PushSend(
                        user_id=target,
                        event_id=event_id,
                        message_type=PushMessageType.INSTALL_WARNING,
                    )
                )
            except Exception as error:
                logger.error(
                    "install_push_failed event_id=%s user_id=%s code=%s",
                    event_id,
                    target,
                    type(error).__name__,
                )
        return AppInstallRead(
            matched=True, event_id=event_id, related_call=related_call
        )
