import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import (
    Base,
    Family,
    FamilyMember,
    FamilyRole,
    NotificationRequest,
    NotificationType,
    SecurityEvent,
    User,
    VerificationAnswer,
    VerificationRequest,
    VerificationResponse,
    VerificationStatus,
)
from app.repositories.event import SecurityEventRepository
from app.repositories.family import FamilyMemberRepository
from app.repositories.notification import NotificationRequestRepository
from app.repositories.verification import VerificationRepository
from tests.conftest import ROOT, migrate
from tests.test_membership import add_family, add_user


async def add_request(
    session: AsyncSession,
) -> tuple[SecurityEvent, VerificationRequest, FamilyMember]:
    """가족·부모·사건과 대기 요청을 같은 트랜잭션에 준비합니다."""
    family, child = await add_family(session)
    parent = await add_user(session, "부모")
    parent_member = await FamilyMemberRepository(session).create(
        family.id, parent.id, FamilyRole.PARENT
    )
    now = datetime.now(UTC)
    event = await SecurityEventRepository(session).create(
        family.id, parent_member.id, "ALERT", "CHILD_IMPERSONATION", now
    )
    request = await VerificationRepository(session).create(
        event.id,
        family.id,
        parent_member.id,
        VerificationStatus.PENDING,
        now,
        now + timedelta(minutes=10),
    )
    return event, request, child


def test_stage_migration_preserves_membership(
    engine: AsyncEngine, runner: asyncio.Runner
) -> None:
    """이번 단계만 되돌려도 기존 계정이 남고 재적용 후 ORM과 일치합니다."""

    def verify(connection: Connection) -> None:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
        user_id = uuid4()
        connection.execute(
            User.__table__.insert().values(
                id=user_id,
                email="kept@example.com",
                name="기존 계정",
                hashed_password="hash",
                is_active=True,
                is_superuser=False,
                is_verified=False,
            )
        )
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0001_membership")
        assert set(inspect(connection).get_table_names()) == {
            "alembic_version",
            "users",
            "families",
            "family_members",
            "family_invitations",
        }
        assert connection.scalar(select(User.id)) == user_id
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_type t JOIN pg_namespace n "
                    "ON n.oid=t.typnamespace WHERE n.nspname=current_schema() "
                    "AND t.typname IN "
                    "('verification_status', 'verification_answer', "
                    "'notification_type')"
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


def test_storage_roundtrip_counts_and_notification(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """미응답 대상·응답 집계·알림 의도가 함께 저장되고 다시 조회됩니다."""

    async def scenario() -> None:
        event, request, child = await add_request(session)
        other_user = await add_user(session, "다른 자녀")
        other = await FamilyMemberRepository(session).create(
            request.family_id, other_user.id, FamilyRole.CHILD
        )
        repository = VerificationRepository(session)
        targets = await repository.add_targets(request.id, [child.id, other.id])
        assert all(row.response is None and row.responded_at is None for row in targets)
        assert await repository.counts(request.id) == (2, 0, 0)
        assert event.status == "OPEN" and event.risk_level is None
        assert event.summary is None and event.created_at.tzinfo is not None
        await repository.record_response(
            targets[0], VerificationAnswer.NOT_SENT, datetime.now(UTC)
        )
        assert await repository.counts(request.id) == (2, 1, 0)
        await repository.record_response(
            targets[1], VerificationAnswer.SENT, datetime.now(UTC)
        )
        await repository.finalize(
            request, VerificationStatus.CONFIRMED, datetime.now(UTC)
        )
        notification = await NotificationRequestRepository(session).create(
            event.id, request.id, child.user_id, NotificationType.VERIFY_CONFIRMED
        )
        event_id, request_id, child_id, notification_id = (
            event.id,
            request.id,
            child.id,
            notification.id,
        )
        await session.commit()
        session.expunge_all()
        assert (await SecurityEventRepository(session).get(event_id)).status == "OPEN"
        stored = await repository.get_by_event(event_id)
        assert stored.id == request_id and stored.status == VerificationStatus.CONFIRMED
        assert stored.finalized_at.tzinfo is not None
        assert await repository.counts(request_id) == (2, 2, 1)
        assert (await repository.get_response(request_id, child_id)).response == (
            VerificationAnswer.NOT_SENT
        )
        assert (
            await session.get(NotificationRequest, notification_id)
        ).created_at.tzinfo
        assert await repository.get(uuid4()) is None
        assert await repository.get_by_event(uuid4()) is None
        assert await repository.get_response(request_id, uuid4()) is None
        assert await repository.counts(uuid4()) == (0, 0, 0)
        assert await SecurityEventRepository(session).get(uuid4()) is None

    runner.run(scenario())


@pytest.mark.parametrize(
    "violation",
    [
        "duplicate_event",
        "missing_event",
        "missing_family",
        "missing_parent",
        "duplicate_target",
        "missing_target_request",
        "missing_member",
        "answer_without_time",
        "time_without_answer",
        "pending_finalized",
        "finalized_without_time",
        "expiry_before_created",
        "finalized_before_created",
        "duplicate_notification",
        "missing_notification_event",
        "missing_notification_request",
        "missing_notification_user",
        "invalid_status",
        "invalid_answer",
        "invalid_notification_type",
    ],
)
def test_database_constraints(
    violation: str, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """중복·없는 참조·응답 및 시각 모순·잘못된 ENUM을 DB가 차단합니다."""

    async def scenario() -> None:
        event, request, child = await add_request(session)
        repository = VerificationRepository(session)
        target = (await repository.add_targets(request.id, [child.id]))[0]
        notifications = NotificationRequestRepository(session)
        notification = await notifications.create(
            event.id, request.id, child.user_id, NotificationType.VERIFY_REQUEST
        )
        await session.commit()
        with pytest.raises(DBAPIError):
            if violation == "duplicate_event":
                await repository.create(
                    event.id,
                    request.family_id,
                    request.parent_member_id,
                    VerificationStatus.PENDING,
                    request.created_at,
                    request.expires_at,
                )
            elif violation in {"missing_event", "missing_family", "missing_parent"}:
                attribute = {
                    "missing_event": "event_id",
                    "missing_family": "family_id",
                    "missing_parent": "parent_member_id",
                }[violation]
                setattr(request, attribute, uuid4())
            elif violation == "duplicate_target":
                await repository.add_targets(request.id, [child.id])
            elif violation == "missing_target_request":
                target.verification_id = uuid4()
            elif violation == "missing_member":
                target.member_id = uuid4()
            elif violation == "answer_without_time":
                target.response = VerificationAnswer.SENT
            elif violation == "time_without_answer":
                target.responded_at = datetime.now(UTC)
            elif violation == "pending_finalized":
                request.finalized_at = datetime.now(UTC)
            elif violation == "finalized_without_time":
                request.status = VerificationStatus.DENIED
            elif violation == "expiry_before_created":
                request.expires_at = request.created_at - timedelta(seconds=1)
            elif violation == "finalized_before_created":
                request.status = VerificationStatus.EXPIRED
                request.finalized_at = request.created_at - timedelta(seconds=1)
            elif violation == "duplicate_notification":
                await notifications.create(
                    event.id, request.id, child.user_id, NotificationType.VERIFY_REQUEST
                )
            elif violation.startswith("missing_notification_"):
                attribute = {
                    "missing_notification_event": "event_id",
                    "missing_notification_request": "verification_id",
                    "missing_notification_user": "user_id",
                }[violation]
                setattr(notification, attribute, uuid4())
            elif violation == "invalid_status":
                request.status = "INVALID"
            elif violation == "invalid_answer":
                target.response = "INVALID"
                target.responded_at = datetime.now(UTC)
            else:
                notification.message_type = "INVALID"
            await session.flush()
        await session.rollback()

    runner.run(scenario())


def test_caller_rollback_removes_all_new_records(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """저장소가 commit하지 않아 사건부터 알림까지 함께 롤백됩니다."""

    async def scenario() -> None:
        event, request, child = await add_request(session)
        await VerificationRepository(session).add_targets(request.id, [child.id])
        await NotificationRequestRepository(session).create(
            event.id, request.id, child.user_id, NotificationType.VERIFY_REQUEST
        )
        await session.rollback()
        for model in (
            SecurityEvent,
            VerificationRequest,
            VerificationResponse,
            NotificationRequest,
            Family,
            FamilyMember,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0

    runner.run(scenario())


def test_notification_failure_rolls_back_answer_and_finalization(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """알림 저장 실패 후 호출자 롤백이 응답·최종 상태도 되돌립니다."""

    async def scenario() -> None:
        event, request, child = await add_request(session)
        repository = VerificationRepository(session)
        target = (await repository.add_targets(request.id, [child.id]))[0]
        request_id, child_id = request.id, child.id
        await session.commit()
        await repository.record_response(
            target, VerificationAnswer.SENT, datetime.now(UTC)
        )
        await repository.finalize(
            request, VerificationStatus.CONFIRMED, datetime.now(UTC)
        )
        with pytest.raises(IntegrityError):
            await NotificationRequestRepository(session).create(
                event.id, request.id, uuid4(), NotificationType.VERIFY_CONFIRMED
            )
        await session.rollback()
        assert (await repository.get(request_id)).status == VerificationStatus.PENDING
        assert (await repository.get_response(request_id, child_id)).response is None

    runner.run(scenario())


def test_expiry_boundary_and_zero_targets(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """만료 정각 요청만 선택하고 확정·미래 요청과 빈 대상을 구분합니다."""

    async def scenario() -> None:
        event, due, _ = await add_request(session)
        _, future, _ = await add_request(session)
        _, closed, _ = await add_request(session)
        _, empty, _ = await add_request(session)
        repository = VerificationRepository(session)
        now = due.expires_at
        future.expires_at = now + timedelta(seconds=1)
        await repository.finalize(
            closed, VerificationStatus.CONFIRMED, closed.created_at
        )
        empty.expires_at = empty.created_at
        await repository.finalize(empty, VerificationStatus.EXPIRED, empty.created_at)
        await session.commit()
        assert await repository.list_expired_ids(now - timedelta(microseconds=1)) == []
        assert await repository.list_expired_ids(now) == [due.id]
        assert await repository.counts(empty.id) == (0, 0, 0)
        assert await repository.add_targets(empty.id, []) == []
        assert (await SecurityEventRepository(session).get(event.id)).status == "OPEN"

    runner.run(scenario())


def test_request_lock_and_fresh_response(
    session: AsyncSession, engine: AsyncEngine, runner: asyncio.Runner
) -> None:
    """요청 잠금이 경합을 막고 잠금 후 조회는 다른 세션의 갱신을 반영합니다."""

    async def scenario() -> None:
        _, request, child = await add_request(session)
        repository = VerificationRepository(session)
        target = (await repository.add_targets(request.id, [child.id]))[0]
        await session.commit()
        async with AsyncSession(engine, expire_on_commit=False) as competing:
            other = VerificationRepository(competing)
            cached = await other.get(request.id)
            cached_response = await other.get_response(request.id, child.id)
            assert cached_response.response is None
            await competing.commit()
            await repository.get(request.id, for_update=True)
            await competing.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as error:
                await other.get(request.id, for_update=True)
            assert error.value.orig.sqlstate == "55P03"
            await competing.rollback()
            await repository.record_response(
                target, VerificationAnswer.SENT, datetime.now(UTC)
            )
            await repository.finalize(
                request, VerificationStatus.CONFIRMED, datetime.now(UTC)
            )
            await session.commit()
            refreshed = await other.get(request.id, for_update=True)
            assert (
                refreshed is cached and refreshed.status == VerificationStatus.CONFIRMED
            )
            response = await other.get_response(request.id, child.id)
            assert (
                response is cached_response
                and response.response == VerificationAnswer.SENT
            )

    runner.run(scenario())
