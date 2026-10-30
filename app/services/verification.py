import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.family import FamilyMember, FamilyRole
from app.models.notification import NotificationType
from app.models.verification import (
    VerificationAnswer,
    VerificationRequest,
    VerificationStatus,
)
from app.repositories.event import SecurityEventRepository
from app.repositories.family import FamilyMemberRepository
from app.repositories.notification import NotificationRequestRepository
from app.repositories.verification import VerificationRepository
from app.schemas.verification import VerificationRead
from app.services.family import FamilyService
from app.services.transaction import transaction

logger = logging.getLogger(__name__)


class VerificationService:
    """가족 확인의 권한·응답 집계·만료와 알림 요청을 함께 처리합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """모든 저장소에 동일한 트랜잭션의 세션을 연결합니다."""
        self.session = session
        self.events = SecurityEventRepository(session)
        self.members = FamilyMemberRepository(session)
        self.verifications = VerificationRepository(session)
        self.notifications = NotificationRequestRepository(session)
        self.families = FamilyService(session)

    async def _require_parent(
        self, family_id: UUID, parent_member_id: UUID
    ) -> FamilyMember:
        parent = await self.members.get_by_id(parent_member_id)
        if (
            parent is None
            or parent.family_id != family_id
            or parent.role != FamilyRole.PARENT
        ):
            raise ServiceError(409, "EVENT_PARENT_MISMATCH")
        return parent

    async def create(self, event_id: UUID) -> VerificationRequest:
        """저장된 사칭 사건에 확인 요청과 대상·알림 요청을 한 번만 생성합니다."""
        try:
            async with transaction(self.session):
                event = await self.events.get(event_id, for_update=True)
                if event is None:
                    raise ServiceError(404, "EVENT_NOT_FOUND")
                if event.classification != "CHILD_IMPERSONATION":
                    raise ServiceError(409, "EVENT_NOT_VERIFIABLE")
                parent = await self._require_parent(
                    event.family_id, event.parent_member_id
                )
                existing = await self.verifications.get_by_event(event_id)
                if existing is not None:
                    return existing
                children = [
                    member
                    for member in await self.members.list_by_family(event.family_id)
                    if member.role == FamilyRole.CHILD
                ]
                now = datetime.now(UTC)
                request = await self.verifications.create(
                    event.id,
                    event.family_id,
                    parent.id,
                    VerificationStatus.PENDING
                    if children
                    else VerificationStatus.EXPIRED,
                    now,
                    now + timedelta(minutes=10) if children else now,
                    finalized_at=None if children else now,
                )
                await self.verifications.add_targets(
                    request.id, [child.id for child in children]
                )
                for child in children:
                    await self.notifications.create(
                        event.id,
                        request.id,
                        child.user_id,
                        NotificationType.VERIFY_REQUEST,
                    )
                await self.notifications.create(
                    event.id,
                    request.id,
                    parent.user_id,
                    NotificationType.VERIFY_WAITING
                    if children
                    else NotificationType.VERIFY_EXPIRED,
                )
        except ServiceError as error:
            logger.warning("verification_create_rejected code=%s", error.detail)
            raise
        logger.info("verification_created id=%s status=%s", request.id, request.status)
        return request

    async def _require_request(self, verification_id: UUID) -> VerificationRequest:
        request = await self.verifications.get(verification_id, for_update=True)
        if request is None:
            raise ServiceError(404, "VERIFICATION_NOT_FOUND")
        return request

    async def _finalize(
        self, request: VerificationRequest, status: VerificationStatus, now: datetime
    ) -> None:
        parent = await self._require_parent(request.family_id, request.parent_member_id)
        await self.verifications.finalize(request, status, now)
        message_type = {
            VerificationStatus.CONFIRMED: NotificationType.VERIFY_CONFIRMED,
            VerificationStatus.DENIED: NotificationType.VERIFY_DENIED,
            VerificationStatus.EXPIRED: NotificationType.VERIFY_EXPIRED,
        }[status]
        await self.notifications.create(
            request.event_id, request.id, parent.user_id, message_type
        )

    async def get_detail(
        self, verification_id: UUID, user_id: UUID
    ) -> VerificationRead:
        """가족 멤버에게 현재 상태를 반환하며 지난 기한은 만료로 확정합니다."""
        expired = False
        async with transaction(self.session):
            request = await self._require_request(verification_id)
            member = await self.families.require_member(request.family_id, user_id)
            now = datetime.now(UTC)
            if (
                request.status == VerificationStatus.PENDING
                and request.expires_at <= now
            ):
                await self._finalize(request, VerificationStatus.EXPIRED, now)
                expired = True
            target_count, responded_count, _ = await self.verifications.counts(
                request.id
            )
            response = await self.verifications.get_response(request.id, member.id)
            result = VerificationRead(
                id=request.id,
                event_id=request.event_id,
                status=request.status,
                expires_at=request.expires_at,
                target_count=target_count,
                responded_count=responded_count,
                my_response=response.response if response else None,
            )
        if expired:
            logger.info("verification_expired id=%s", verification_id)
        return result

    async def respond(
        self, verification_id: UUID, user_id: UUID, answer: VerificationAnswer
    ) -> VerificationRead:
        """대상 자녀의 최초 응답을 기록하고 집계 결과와 알림을 함께 확정합니다."""
        expired = False
        async with transaction(self.session):
            request = await self._require_request(verification_id)
            member = await self.families.require_member(request.family_id, user_id)
            response = await self.verifications.get_response(request.id, member.id)
            if member.role != FamilyRole.CHILD or response is None:
                raise ServiceError(403, "VERIFICATION_RESPONDER_REQUIRED")
            now = datetime.now(UTC)
            if request.status == VerificationStatus.EXPIRED:
                expired = True
            elif request.status != VerificationStatus.PENDING:
                raise ServiceError(409, "VERIFICATION_FINALIZED")
            elif request.expires_at <= now:
                await self._finalize(request, VerificationStatus.EXPIRED, now)
                expired = True
            elif response.response is not None:
                raise ServiceError(409, "VERIFICATION_ALREADY_RESPONDED")
            else:
                await self.verifications.record_response(response, answer, now)
                (
                    target_count,
                    responded_count,
                    sent_count,
                ) = await self.verifications.counts(request.id)
                if sent_count:
                    await self._finalize(request, VerificationStatus.CONFIRMED, now)
                elif target_count > 0 and responded_count == target_count:
                    await self._finalize(request, VerificationStatus.DENIED, now)
                result = VerificationRead(
                    id=request.id,
                    event_id=request.event_id,
                    status=request.status,
                    expires_at=request.expires_at,
                    target_count=target_count,
                    responded_count=responded_count,
                    my_response=response.response,
                )
        # 만료 저장을 commit한 다음 오류를 반환해야 전체 변경이 롤백되지 않는다.
        if expired:
            logger.info("verification_expired_response id=%s", verification_id)
            raise ServiceError(410, "VERIFICATION_EXPIRED")
        logger.info(
            "verification_responded id=%s status=%s", verification_id, result.status
        )
        return result

    async def expire_due(self) -> int:
        """기한이 지난 대기 요청과 부모 알림을 확정하고 처리 수를 반환합니다."""
        expired_ids = []
        async with transaction(self.session):
            verification_ids = await self.verifications.list_expired_ids(
                datetime.now(UTC)
            )
            for verification_id in verification_ids:
                request = await self.verifications.get(verification_id, for_update=True)
                now = datetime.now(UTC)
                if (
                    request is not None
                    and request.status == VerificationStatus.PENDING
                    and request.expires_at <= now
                ):
                    await self._finalize(request, VerificationStatus.EXPIRED, now)
                    expired_ids.append(verification_id)
        for verification_id in expired_ids:
            logger.info("verification_expired id=%s", verification_id)
        return len(expired_ids)
