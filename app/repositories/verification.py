from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.verification import (
    VerificationAnswer,
    VerificationRequest,
    VerificationResponse,
    VerificationStatus,
)


class VerificationRepository:
    """확인 요청·응답을 저장하며 상태 판정과 commit은 수행하지 않습니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 세션을 저장합니다."""
        self.session = session

    async def create(
        self,
        event_id: UUID,
        family_id: UUID,
        parent_member_id: UUID,
        status: VerificationStatus,
        created_at: datetime,
        expires_at: datetime,
        *,
        finalized_at: datetime | None = None,
    ) -> VerificationRequest:
        """호출자가 결정한 상태·시각으로 확인 요청을 저장합니다."""
        request = VerificationRequest(
            event_id=event_id,
            family_id=family_id,
            parent_member_id=parent_member_id,
            status=status,
            created_at=created_at,
            expires_at=expires_at,
            finalized_at=finalized_at,
        )
        self.session.add(request)
        await self.session.flush()
        return request

    async def add_targets(
        self, verification_id: UUID, member_ids: list[UUID]
    ) -> list[VerificationResponse]:
        """대상 자녀들을 미응답 상태로 저장합니다."""
        responses = [
            VerificationResponse(verification_id=verification_id, member_id=member_id)
            for member_id in member_ids
        ]
        self.session.add_all(responses)
        await self.session.flush()
        return responses

    async def get(
        self, verification_id: UUID, *, for_update: bool = False
    ) -> VerificationRequest | None:
        """확인 요청을 조회하고 필요하면 최신 상태로 행을 잠급니다."""
        statement = select(VerificationRequest).where(
            VerificationRequest.id == verification_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return await self.session.scalar(statement)

    async def get_by_event(self, event_id: UUID) -> VerificationRequest | None:
        """사건의 확인 요청을 최신 상태로 조회합니다."""
        return await self.session.scalar(
            select(VerificationRequest)
            .where(VerificationRequest.event_id == event_id)
            .execution_options(populate_existing=True)
        )

    async def get_response(
        self, verification_id: UUID, member_id: UUID
    ) -> VerificationResponse | None:
        """대상 자녀의 응답을 캐시 대신 최신 저장 값으로 조회합니다."""
        return await self.session.scalar(
            select(VerificationResponse)
            .where(
                VerificationResponse.verification_id == verification_id,
                VerificationResponse.member_id == member_id,
            )
            .execution_options(populate_existing=True)
        )

    async def counts(self, verification_id: UUID) -> tuple[int, int, int]:
        """대상 수·응답 수·발송 확인 수를 한 번에 조회합니다."""
        row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count(VerificationResponse.response),
                    func.count().filter(
                        VerificationResponse.response == VerificationAnswer.SENT
                    ),
                ).where(VerificationResponse.verification_id == verification_id)
            )
        ).one()
        return row[0], row[1], row[2]

    async def record_response(
        self,
        response: VerificationResponse,
        answer: VerificationAnswer,
        responded_at: datetime,
    ) -> None:
        """호출자가 검증한 자녀 응답과 시각을 기록합니다."""
        response.response = answer
        response.responded_at = responded_at
        await self.session.flush()

    async def finalize(
        self,
        request: VerificationRequest,
        status: VerificationStatus,
        finalized_at: datetime,
    ) -> None:
        """호출자가 판정한 최종 상태와 확정 시각을 기록합니다."""
        request.status = status
        request.finalized_at = finalized_at
        await self.session.flush()

    async def list_expired_ids(self, now: datetime) -> list[UUID]:
        """지정 시각까지 기한이 지난 대기 요청의 ID를 조회합니다."""
        statement = (
            select(VerificationRequest.id)
            .where(
                VerificationRequest.status == VerificationStatus.PENDING,
                VerificationRequest.expires_at <= now,
            )
            .order_by(VerificationRequest.expires_at, VerificationRequest.id)
        )
        return list(await self.session.scalars(statement))
