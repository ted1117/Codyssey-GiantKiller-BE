from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.family import Family, FamilyMember, FamilyRole


class FamilyRepository:
    """가족 데이터를 조회·저장하며 commit은 호출자가 담당합니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 세션을 저장합니다."""
        self.session = session

    async def create(self) -> Family:
        """가족을 생성하고 flush합니다."""
        family = Family()
        self.session.add(family)
        await self.session.flush()
        return family

    async def get(self, family_id: UUID, *, for_update: bool = False) -> Family | None:
        """가족을 조회하고 필요하면 트랜잭션 종료까지 행을 잠급니다."""
        statement = select(Family).where(Family.id == family_id)
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return await self.session.scalar(statement)

    async def list_by_user(self, user_id: UUID) -> list[Family]:
        """사용자가 속한 가족을 생성 시각 순으로 조회합니다."""
        statement = (
            select(Family)
            .join(FamilyMember, FamilyMember.family_id == Family.id)
            .where(FamilyMember.user_id == user_id)
            .order_by(Family.created_at, Family.id)
        )
        return list(await self.session.scalars(statement))


class FamilyMemberRepository:
    """가족 소속 데이터를 조회·저장하며 권한 정책은 판단하지 않습니다."""

    def __init__(self, session: AsyncSession) -> None:
        """사용할 세션을 저장합니다."""
        self.session = session

    async def create(
        self,
        family_id: UUID,
        user_id: UUID,
        role: FamilyRole,
        *,
        is_representative: bool = False,
    ) -> FamilyMember:
        """멤버를 생성하고 flush합니다."""
        member = FamilyMember(
            family_id=family_id,
            user_id=user_id,
            role=role,
            is_representative=is_representative,
        )
        self.session.add(member)
        await self.session.flush()
        return member

    async def get(self, family_id: UUID, user_id: UUID) -> FamilyMember | None:
        """가족과 사용자에 해당하는 멤버를 계정 정보와 함께 조회합니다."""
        statement = (
            select(FamilyMember)
            .where(FamilyMember.family_id == family_id, FamilyMember.user_id == user_id)
            .options(selectinload(FamilyMember.user))
        )
        return await self.session.scalar(statement)

    async def list_by_family(self, family_id: UUID) -> list[FamilyMember]:
        """가족 멤버와 이름을 추가 지연 조회 없이 읽을 수 있도록 조회합니다."""
        statement = (
            select(FamilyMember)
            .where(FamilyMember.family_id == family_id)
            .options(selectinload(FamilyMember.user))
            .order_by(FamilyMember.joined_at, FamilyMember.id)
        )
        return list(await self.session.scalars(statement))

    async def get_representative(self, family_id: UUID) -> FamilyMember | None:
        """가족 대표와 계정 정보를 조회합니다."""
        statement = (
            select(FamilyMember)
            .where(
                FamilyMember.family_id == family_id,
                FamilyMember.is_representative.is_(True),
            )
            .options(selectinload(FamilyMember.user))
        )
        return await self.session.scalar(statement)

    async def count_by_role(self, family_id: UUID, role: FamilyRole) -> int:
        """가족 내 지정 역할의 멤버 수를 조회합니다."""
        statement = (
            select(func.count())
            .select_from(FamilyMember)
            .where(FamilyMember.family_id == family_id, FamilyMember.role == role)
        )
        return (await self.session.scalar(statement)) or 0
