from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationRequest, NotificationType


class NotificationRequestRepository:
    """알림 요청을 저장하며 실제 발송과 commit은 수행하지 않습니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 세션을 저장합니다."""
        self.session = session

    async def create(
        self,
        event_id: UUID,
        verification_id: UUID,
        user_id: UUID,
        message_type: NotificationType,
    ) -> NotificationRequest:
        """호출자가 결정한 대상과 유형으로 알림 요청을 저장합니다."""
        notification = NotificationRequest(
            event_id=event_id,
            verification_id=verification_id,
            user_id=user_id,
            message_type=message_type,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification
