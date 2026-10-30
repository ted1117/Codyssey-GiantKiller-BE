import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Base, Family, FamilyInvitation, FamilyMember, FamilyRole, User
from app.repositories.family import FamilyMemberRepository, FamilyRepository
from app.repositories.invitation import FamilyInvitationRepository
from app.repositories.user import UserRepository
from tests.conftest import migrate


async def add_user(session: AsyncSession, name: str = "자녀") -> User:
    """트랜잭션을 확정하지 않고 테스트 계정을 저장합니다."""
    user = User(
        email=f"{uuid4().hex}@example.com", name=name, hashed_password="test-hash"
    )
    session.add(user)
    await session.flush()
    return user


async def add_family(
    session: AsyncSession,
) -> tuple[Family, FamilyMember]:
    """가족과 대표 자녀를 동일 트랜잭션에 저장합니다."""
    user = await add_user(session)
    family = await FamilyRepository(session).create()
    member = await FamilyMemberRepository(session).create(
        family.id, user.id, FamilyRole.CHILD, is_representative=True
    )
    return family, member


async def add_invitation(session: AsyncSession) -> FamilyInvitation:
    """토큰 해시만 저장한 초대를 생성합니다."""
    family, representative = await add_family(session)
    return await FamilyInvitationRepository(session).create(
        family.id,
        representative.id,
        FamilyRole.PARENT,
        uuid4().hex * 2,
        datetime.now(UTC) + timedelta(hours=24),
    )


def test_migration_matches_models_and_roundtrips(
    engine: AsyncEngine, runner: asyncio.Runner
) -> None:
    """실제 마이그레이션과 ORM을 대조하고 downgrade 후 재적용합니다."""

    def verify(connection: Connection) -> None:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
        migrate(connection, "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_type t JOIN pg_namespace n "
                    "ON n.oid=t.typnamespace WHERE t.typname='family_role' "
                    "AND n.nspname=current_schema()"
                )
            )
            == 0
        )
        migrate(connection)
        assert compare_metadata(context, Base.metadata) == []

    async def scenario() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(verify)

    runner.run(scenario())


def test_fastapi_users_adapter(session: AsyncSession, runner: asyncio.Runner) -> None:
    """계정 생성 기본값과 FastAPI Users의 이메일·ID 조회를 확인합니다."""

    async def scenario() -> None:
        repository = UserRepository(session)
        user = await repository.create(
            {"email": "child@example.com", "name": "자녀", "hashed_password": "hash"}
        )
        assert user.is_active and not user.is_verified and not user.is_superuser
        assert (await repository.get(user.id)).name == "자녀"
        assert (await repository.get_by_email("CHILD@example.com")).id == user.id
        assert await repository.get(uuid4()) is None
        with pytest.raises(IntegrityError):
            await repository.create(
                {"email": user.email, "name": "중복", "hashed_password": "hash"}
            )
        await session.rollback()

    runner.run(scenario())


def test_family_queries_and_multiple_memberships(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """다중 가족과 멤버 이름 조회가 가족별로 분리되는지 확인합니다."""

    async def scenario() -> None:
        families = FamilyRepository(session)
        members = FamilyMemberRepository(session)
        family, representative = await add_family(session)
        other_family, _ = await add_family(session)
        parent = await add_user(session, "부모님")
        await members.create(family.id, parent.id, FamilyRole.PARENT)
        await members.create(other_family.id, parent.id, FamilyRole.PARENT)
        await session.commit()
        session.expunge_all()
        assert {f.id for f in await families.list_by_user(parent.id)} == {
            family.id,
            other_family.id,
        }
        assert [f.id for f in await families.list_by_user(representative.user_id)] == [
            family.id
        ]
        rows = await members.list_by_family(family.id)
        assert {row.user.name for row in rows} == {"자녀", "부모님"}
        assert (await members.get_representative(family.id)).id == representative.id
        assert (await members.get(family.id, parent.id)).role == FamilyRole.PARENT
        assert await members.get(other_family.id, representative.user_id) is None
        assert await members.count_by_role(family.id, FamilyRole.PARENT) == 1
        assert await families.get(uuid4()) is None
        assert await members.list_by_family(uuid4()) == []

    runner.run(scenario())


@pytest.mark.parametrize(
    "violation",
    [
        "duplicate_member",
        "second_representative",
        "parent_representative",
        "missing_user",
    ],
)
def test_membership_database_constraints(
    violation: str, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """중복 가입·중복 대표·부모 대표·없는 계정 참조를 DB가 거부합니다."""

    async def scenario() -> None:
        family, representative = await add_family(session)
        other = await add_user(session)
        await session.commit()
        user_id = (
            representative.user_id if violation == "duplicate_member" else other.id
        )
        if violation == "missing_user":
            user_id = uuid4()
        role = (
            FamilyRole.PARENT
            if violation == "parent_representative"
            else FamilyRole.CHILD
        )
        with pytest.raises(IntegrityError):
            await FamilyMemberRepository(session).create(
                family.id,
                user_id,
                role,
                is_representative=violation
                in {"second_representative", "parent_representative"},
            )
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(FamilyMember)) == 1

    runner.run(scenario())


def test_family_and_membership_rollback(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """호출자가 롤백하면 생성 중인 가족과 대표가 함께 사라집니다."""

    async def scenario() -> None:
        await add_family(session)
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(Family)) == 0
        assert await session.scalar(select(func.count()).select_from(FamilyMember)) == 0

    runner.run(scenario())


def test_invitation_queries_acceptance_and_rollback(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """가족별 초대 조회와 멤버 등록·초대 갱신의 공동 롤백을 확인합니다."""

    async def scenario() -> None:
        invitation = await add_invitation(session)
        user = await add_user(session, "부모님")
        await session.commit()
        repository = FamilyInvitationRepository(session)
        assert await repository.get(uuid4(), invitation.id) is None
        assert await repository.get_by_token_hash("missing") is None
        assert (
            await repository.get(invitation.family_id, invitation.id)
        ).id == invitation.id
        assert invitation.created_at.tzinfo is not None
        assert invitation.expires_at.tzinfo is not None
        invitation_id, family_id, token_hash = (
            invitation.id,
            invitation.family_id,
            invitation.token_hash,
        )
        member = await FamilyMemberRepository(session).create(
            family_id, user.id, FamilyRole.PARENT
        )
        member_id = member.id
        await repository.mark_accepted(invitation, user.id, datetime.now(UTC))
        await session.rollback()
        assert await session.get(FamilyMember, member_id) is None
        invitation = await repository.get_by_token_hash(token_hash, for_update=True)
        assert invitation.accepted_at is None
        assert invitation.accepted_by_user_id is None
        await repository.mark_revoked(invitation, datetime.now(UTC))
        await session.commit()
        session.expunge_all()
        invitation = await repository.get(family_id, invitation_id)
        assert invitation.revoked_at is not None

    runner.run(scenario())


def test_member_and_invitation_acceptance_persist_together(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """멤버 등록과 수락 기록이 한 번의 commit으로 함께 저장됩니다."""

    async def scenario() -> None:
        invitation = await add_invitation(session)
        user = await add_user(session, "부모님")
        await session.commit()
        invitation_id, family_id, user_id = invitation.id, invitation.family_id, user.id
        repository = FamilyInvitationRepository(session)
        invitation = await repository.get(family_id, invitation_id, for_update=True)
        await FamilyMemberRepository(session).create(
            family_id, user_id, invitation.role
        )
        await repository.mark_accepted(invitation, user_id, datetime.now(UTC))
        await session.commit()
        session.expunge_all()
        invitation = await repository.get(family_id, invitation_id)
        member = await FamilyMemberRepository(session).get(family_id, user_id)
        assert member.role == FamilyRole.PARENT
        assert invitation.accepted_by_user_id == member.user_id
        assert invitation.accepted_at.tzinfo is not None
        assert invitation.revoked_at is None

    runner.run(scenario())


@pytest.mark.parametrize(
    "violation", ["duplicate_hash", "missing_acceptor", "two_outcomes"]
)
def test_invitation_database_constraints(
    violation: str, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """중복 토큰과 모순된 초대 이력을 DB가 거부합니다."""

    async def scenario() -> None:
        invitation = await add_invitation(session)
        user = await add_user(session)
        await session.commit()
        repository = FamilyInvitationRepository(session)
        with pytest.raises(IntegrityError):
            if violation == "duplicate_hash":
                await repository.create(
                    invitation.family_id,
                    invitation.created_by_member_id,
                    invitation.role,
                    invitation.token_hash,
                    invitation.expires_at,
                )
            elif violation == "missing_acceptor":
                invitation.accepted_at = datetime.now(UTC)
                await session.flush()
            else:
                await repository.mark_accepted(invitation, user.id, datetime.now(UTC))
                await repository.mark_revoked(invitation, datetime.now(UTC))
        await session.rollback()

    runner.run(scenario())


@pytest.mark.parametrize("locked_row", ["family", "invitation"])
def test_row_locks_and_fresh_invitation_state(
    locked_row: str, session: AsyncSession, engine: AsyncEngine, runner: asyncio.Runner
) -> None:
    """행 잠금이 다른 세션을 막고, 재조회가 캐시된 초대 상태를 갱신합니다."""

    async def scenario() -> None:
        invitation = await add_invitation(session)
        family_id, invitation_id = invitation.family_id, invitation.id
        await session.commit()
        async with AsyncSession(engine, expire_on_commit=False) as competing:
            repository = FamilyInvitationRepository(competing)
            cached = await repository.get(family_id, invitation_id)
            assert cached.revoked_at is None
            await competing.commit()
            if locked_row == "family":
                await FamilyRepository(session).get(family_id, for_update=True)
            else:
                await FamilyInvitationRepository(session).get_by_token_hash(
                    invitation.token_hash, for_update=True
                )
            await competing.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as error:
                if locked_row == "family":
                    await FamilyRepository(competing).get(family_id, for_update=True)
                else:
                    await repository.get(family_id, invitation_id, for_update=True)
            assert error.value.orig.sqlstate == "55P03"
            await competing.rollback()
            await FamilyInvitationRepository(session).mark_revoked(
                invitation, datetime.now(UTC)
            )
            await session.commit()
            refreshed = await repository.get(family_id, invitation_id, for_update=True)
            assert refreshed.revoked_at is not None

    runner.run(scenario())
