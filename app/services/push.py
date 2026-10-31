import logging

from firebase_admin import messaging
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.fcm import FCMClient
from app.repositories.push_registration import PushRegistrationRepository
from app.schemas.push import PushMessageType, PushSend, PushSendResult
from app.services.transaction import transaction

logger = logging.getLogger(__name__)

_MESSAGES = {
    PushMessageType.RISK_WARNING: (
        "위험 주의 안내",
        "주의가 필요한 상황이에요. 앱에서 내용을 확인해주세요.",
    ),
    PushMessageType.CALL_SUMMARY: (
        "통화 요약 안내",
        "확인할 통화 요약이 있어요. 앱에서 확인해주세요.",
    ),
    PushMessageType.VERIFY_REQUEST: (
        "메시지 발송 확인 요청",
        "부모님이 받은 메시지를 직접 보내셨는지 확인해주세요.",
    ),
    PushMessageType.VERIFY_WAITING: (
        "가족에게 확인 중이에요",
        "송금·앱 설치·인증번호 전달을 잠시 멈춰주세요.",
    ),
    PushMessageType.VERIFY_CONFIRMED: (
        "자녀가 발송을 확인했어요",
        "메시지나 링크의 안전을 보장하는 것은 아니에요.",
    ),
    PushMessageType.VERIFY_DENIED: (
        "가족 전원이 발송을 부인했어요",
        "위험 행동을 멈추고 기존에 알고 있던 연락처로 직접 확인해주세요.",
    ),
    PushMessageType.VERIFY_EXPIRED: (
        "가족에게 확인하지 못했어요",
        "기존에 알고 있던 연락처나 다른 가족을 통해 확인해주세요.",
    ),
    PushMessageType.INSTALL_WARNING: (
        "부모님 휴대폰 긴급 확인",
        "위험할 수 있는 앱 설치가 감지됐어요. 바로 부모님께 연락해 주세요.",
    ),
}


class PushService:
    """사건 트랜잭션과 분리된 세션으로 사용자의 모든 앱에 푸시를 보냅니다."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: FCMClient | None = None,
    ) -> None:
        """발송용 세션 팩토리와 교체 가능한 FCM 클라이언트를 저장합니다."""
        self.sessions = sessions
        self.client = client if client is not None else FCMClient()

    async def send(self, message: PushSend) -> PushSendResult:
        """모든 등록 FID에 한 번 시도하고 무효 FID만 조건부로 정리합니다.

        Args:
            message: 내부에서 정한 수신자·사건·알림 유형입니다.

        Returns:
            FCM 접수 성공 수·실패 수와 발송을 건너뛴 사유입니다.
        """
        async with self.sessions() as session:
            repository = PushRegistrationRepository(session)
            if not await repository.is_active_user(message.user_id):
                reason = "USER_INACTIVE_OR_MISSING"
                logger.info(
                    "push_skipped user_id=%s event_id=%s message_type=%s code=%s",
                    message.user_id,
                    message.event_id,
                    message.message_type,
                    reason,
                )
                return PushSendResult(skipped_reason=reason)
            rows = await repository.list_by_user(message.user_id)
            # 세션 종료 뒤 ORM에 접근하지 않고 재등록 검사에 필요한 값만 남긴다.
            targets = [(row.id, row.fid, row.updated_at) for row in rows]

        if not targets:
            logger.info(
                "push_skipped user_id=%s event_id=%s "
                "message_type=%s code=NO_REGISTRATIONS",
                message.user_id,
                message.event_id,
                message.message_type,
            )
            return PushSendResult(skipped_reason="NO_REGISTRATIONS")

        title, body = _MESSAGES[message.message_type]
        data = {
            "event_id": str(message.event_id),
            # message_type은 FCM 예약 data 키라 push_type으로 전달한다.
            "push_type": message.message_type.value,
        }
        if message.verification_id is not None:
            data["verification_id"] = str(message.verification_id)
        if message.message_type == PushMessageType.VERIFY_REQUEST:
            data["actions"] = '["SENT","NOT_SENT"]'

        result = PushSendResult()
        for start in range(0, len(targets), 500):
            batch = targets[start : start + 500]
            try:
                response = await self.client.send(
                    [fid for _, fid, _ in batch], title, body, data
                )
            except Exception as error:
                # SDK 예외 원문에는 대상 FID이 포함될 수 있다.
                for registration_id, _, _ in batch:
                    logger.error(
                        "push_failed user_id=%s registration_id=%s event_id=%s "
                        "message_type=%s code=%s",
                        message.user_id,
                        registration_id,
                        message.event_id,
                        message.message_type,
                        type(error).__name__,
                    )
                result.failure_count += len(batch)
                continue

            invalid = []
            for (registration_id, _, updated_at), sent in zip(
                batch, response.responses, strict=True
            ):
                if sent.success:
                    result.success_count += 1
                    logger.info(
                        "push_sent user_id=%s registration_id=%s "
                        "event_id=%s message_type=%s",
                        message.user_id,
                        registration_id,
                        message.event_id,
                        message.message_type,
                    )
                else:
                    result.failure_count += 1
                    logger.warning(
                        "push_failed user_id=%s registration_id=%s event_id=%s "
                        "message_type=%s code=%s",
                        message.user_id,
                        registration_id,
                        message.event_id,
                        message.message_type,
                        type(sent.exception).__name__,
                    )
                    if isinstance(sent.exception, messaging.UnregisteredError):
                        invalid.append((registration_id, updated_at))

            if invalid:
                try:
                    async with self.sessions() as session, transaction(session):
                        repository = PushRegistrationRepository(session)
                        for registration_id, updated_at in invalid:
                            await repository.delete_if_unchanged(
                                registration_id, message.user_id, updated_at
                            )
                except SQLAlchemyError as error:
                    logger.error(
                        "push_cleanup_failed user_id=%s event_id=%s "
                        "message_type=%s code=%s",
                        message.user_id,
                        message.event_id,
                        message.message_type,
                        type(error).__name__,
                    )
        return result
