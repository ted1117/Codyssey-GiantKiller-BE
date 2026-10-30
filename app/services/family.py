from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServiceError
from app.models.family import FamilyMember, FamilyRole
from app.repositories.family import FamilyMemberRepository, FamilyRepository
from app.schemas.family import FamilyDetail, FamilyMemberRead, FamilyRead
from app.services.transaction import transaction


class FamilyService:
    """가족 생성과 소속·대표 권한을 검증합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """가족 작업에 동일한 세션을 사용합니다."""
        self.session = session
        self.families = FamilyRepository(session)
        self.members = FamilyMemberRepository(session)

    async def create(self, user_id: UUID) -> FamilyDetail:
        """가족과 대표 자녀를 하나의 트랜잭션으로 생성합니다."""
        async with transaction(self.session):
            family = await self.families.create()
            await self.members.create(
                family.id, user_id, FamilyRole.CHILD, is_representative=True
            )
            result = await self.get_detail(family.id, user_id)
        return result

    async def list_by_user(self, user_id: UUID) -> list[FamilyRead]:
        """현재 사용자가 속한 가족 목록을 반환합니다."""
        return [
            FamilyRead.model_validate(family)
            for family in await self.families.list_by_user(user_id)
        ]

    async def require_member(
        self, family_id: UUID, user_id: UUID, *, representative: bool = False
    ) -> FamilyMember:
        """가족의 존재와 소속을 확인하고 필요하면 대표 권한도 검사합니다."""
        member = await self.members.get(family_id, user_id)
        if member is None:
            if await self.families.get(family_id) is None:
                raise ServiceError(404, "FAMILY_NOT_FOUND")
            raise ServiceError(403, "FAMILY_ACCESS_DENIED")
        if representative and not member.is_representative:
            raise ServiceError(403, "REPRESENTATIVE_REQUIRED")
        return member

    async def get_detail(self, family_id: UUID, user_id: UUID) -> FamilyDetail:
        """소속된 가족의 정보와 멤버 목록을 반환합니다."""
        await self.require_member(family_id, user_id)
        family = await self.families.get(family_id)
        members = await self.members.list_by_family(family_id)
        return FamilyDetail(
            **FamilyRead.model_validate(family).model_dump(),
            members=[FamilyMemberRead.model_validate(member) for member in members],
        )
