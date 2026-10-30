import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Connection, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.exceptions import ServiceError
from app.models import CallInstruction, SecurityEvent
from app.models.family import FamilyRole
from app.repositories.family import FamilyMemberRepository
from app.schemas.event import CallAnalysisResult
from app.schemas.push import PushMessageType, PushSend
from app.services.event import EventService
from tests.conftest import ROOT
from tests.test_membership import add_family, add_user


@pytest.mark.parametrize("attention", [False, True])
def test_results_and_commit_before_push(
    session: AsyncSession, engine: AsyncEngine, runner: asyncio.Runner, attention: bool
) -> None:
    """정상은 알리지 않고 의심 결과는 commit 후 해당 가족 자녀에게만 보냅니다."""

    async def scenario() -> None:
        family, child = await add_family(session)
        await add_family(session)
        parent = await add_user(session, "부모")
        await FamilyMemberRepository(session).create(
            family.id, parent.id, FamilyRole.PARENT
        )
        await session.commit()
        targets = []

        async def send(message: PushSend) -> None:
            async with AsyncSession(engine) as observer:
                row = await observer.get(SecurityEvent, message.event_id)
                assert row.analysis_status == "COMPLETED"
                assert row.requires_attention is True
                assert (
                    await observer.scalar(
                        select(func.count()).select_from(CallInstruction)
                    )
                    == 1
                )
            targets.append(message)

        push = AsyncMock()
        push.send.side_effect = send
        service = EventService(session, push)
        event_id = await service.create_call(family.id, parent.id, datetime.now(UTC))
        event = await session.get(SecurityEvent, event_id)
        assert event.analysis_status == "PENDING" and event.classification is None
        assert event.requires_attention is None
        await service.start_analysis(event_id)
        await service.complete_analysis(
            event_id,
            CallAnalysisResult(
                classification="TEST",
                requires_attention=attention,
                summary="private-summary",
                instructions=[
                    {"instruction_type": "TEST", "description": "private-instruction"}
                ],
            ),
        )
        assert event.analysis_status == "COMPLETED"
        assert event.requires_attention is attention
        assert event.status == ("OPEN" if attention else "NOT_REQUIRED")
        assert [m.user_id for m in targets] == ([child.user_id] if attention else [])
        if targets:
            assert targets[0].message_type == PushMessageType.CALL_SUMMARY
        with pytest.raises(ServiceError, match="EVENT_ANALYSIS_STATE_CONFLICT"):
            await service.complete_analysis(
                event_id,
                CallAnalysisResult(classification="TEST", requires_attention=True),
            )

    runner.run(scenario())


@pytest.mark.parametrize("code", ["AI_REQUEST_FAILED", "INVALID_ANALYSIS_RESULT"])
def test_analysis_failure(
    session: AsyncSession, runner: asyncio.Runner, code: str
) -> None:
    """실패는 판단 미정으로 남고 완료 처리나 푸시는 발생하지 않습니다."""

    async def scenario() -> None:
        family, _ = await add_family(session)
        parent = await add_user(session)
        await FamilyMemberRepository(session).create(
            family.id, parent.id, FamilyRole.PARENT
        )
        await session.commit()
        push = AsyncMock()
        service = EventService(session, push)
        event_id = await service.create_call(family.id, parent.id, datetime.now(UTC))
        await service.start_analysis(event_id)
        if code == "INVALID_ANALYSIS_RESULT":
            with pytest.raises(ValidationError):
                CallAnalysisResult.model_validate({"requires_attention": False})
        await service.fail_analysis(event_id, code)
        row = await session.get(SecurityEvent, event_id)
        assert (row.analysis_status, row.status, row.requires_attention) == (
            "FAILED",
            "OPEN",
            None,
        )
        assert row.analysis_error_code == code and row.classification is None
        push.send.assert_not_called()

    runner.run(scenario())


def test_rollback_and_push_failure(
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """지시 저장 실패는 결과를 롤백하며 발송 실패는 완료된 사건을 보존합니다."""

    async def scenario() -> None:
        family, _ = await add_family(session)
        second = await add_user(session)
        await FamilyMemberRepository(session).create(
            family.id, second.id, FamilyRole.CHILD
        )
        parent = await add_user(session)
        await FamilyMemberRepository(session).create(
            family.id, parent.id, FamilyRole.PARENT
        )
        await session.commit()
        push = AsyncMock()
        push.send.side_effect = RuntimeError("private-error")
        service = EventService(session, push)
        event_id = await service.create_call(family.id, parent.id, datetime.now(UTC))
        await service.start_analysis(event_id)
        result = CallAnalysisResult(classification="TEST", requires_attention=True)
        original = service.instructions.create_many
        monkeypatch.setattr(
            service.instructions,
            "create_many",
            AsyncMock(side_effect=RuntimeError("storage")),
        )
        with pytest.raises(RuntimeError):
            await service.complete_analysis(event_id, result)
        row = await session.get(SecurityEvent, event_id)
        assert row.analysis_status == "PROCESSING" and row.requires_attention is None
        push.send.assert_not_called()
        monkeypatch.setattr(service.instructions, "create_many", original)
        await service.complete_analysis(event_id, result)
        assert row.analysis_status == "COMPLETED"
        assert push.send.await_count == 2

    runner.run(scenario())
    assert "private-error" not in caplog.text


def test_permissions_and_states(session: AsyncSession, runner: asyncio.Runner) -> None:
    """자녀·타 가족 생성과 잘못된 상태·사건 유형을 거부합니다."""

    async def scenario() -> None:
        family, child = await add_family(session)
        parent = await add_user(session)
        other, _ = await add_family(session)
        await FamilyMemberRepository(session).create(
            other.id, parent.id, FamilyRole.PARENT
        )
        family_id, child_id, parent_id = family.id, child.user_id, parent.id
        await session.commit()
        service = EventService(session, AsyncMock())
        for user_id, error in [
            (child_id, "PARENT_REQUIRED"),
            (parent_id, "FAMILY_ACCESS_DENIED"),
        ]:
            with pytest.raises(ServiceError, match=error):
                await service.create_call(family_id, user_id, datetime.now(UTC))
        assert (
            await session.scalar(select(func.count()).select_from(SecurityEvent)) == 0
        )
        with pytest.raises(ServiceError, match="EVENT_NOT_FOUND"):
            await service.start_analysis(uuid4())

    runner.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"requires_attention": "false"},
        {"classification": " "},
        {"classification": "x" * 65},
        {"summary": "x\x00"},
        {"instructions": [{"instruction_type": ""}]},
        {"extra": "value"},
    ],
)
def test_result_validation(changes: dict[str, object]) -> None:
    """판정 강제 변환·빈 코드·DB 불가 문자·추가 필드를 거부합니다."""
    with pytest.raises(ValidationError):
        CallAnalysisResult.model_validate(
            {"classification": "TEST", "requires_attention": True, **changes}
        )


def test_downgrade_null_guard(engine: AsyncEngine, runner: asyncio.Runner) -> None:
    """미분류 사건이 있으면 downgrade가 사건을 삭제하지 않고 중단합니다."""

    async def scenario() -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            family, _ = await add_family(session)
            parent = await add_user(session)
            await FamilyMemberRepository(session).create(
                family.id, parent.id, FamilyRole.PARENT
            )
            await session.commit()
            event_id = await EventService(session, AsyncMock()).create_call(
                family.id, parent.id, datetime.now(UTC)
            )

        def downgrade(connection: Connection) -> None:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "0003_push_registrations")

        with pytest.raises(RuntimeError, match="미분류"):
            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
        async with AsyncSession(engine) as observer:
            assert await observer.get(SecurityEvent, event_id) is not None

    runner.run(scenario())


def test_no_children_and_invalid_inputs(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """자녀 없는 사건을 보존하고 잘못된 시각·실패 코드·사건 유형을 거부합니다."""

    async def scenario() -> None:
        from app.repositories.event import SecurityEventRepository
        from app.repositories.family import FamilyRepository

        family = await FamilyRepository(session).create()
        parent = await add_user(session)
        member = await FamilyMemberRepository(session).create(
            family.id, parent.id, FamilyRole.PARENT
        )
        family_id, parent_id, member_id = family.id, parent.id, member.id
        await session.commit()
        push = AsyncMock()
        service = EventService(session, push)
        with pytest.raises(ValueError, match="타임존"):
            await service.create_call(family_id, parent_id, datetime(2026, 10, 31))
        event_id = await service.create_call(family_id, parent_id, datetime.now(UTC))
        with pytest.raises(ServiceError, match="EVENT_ANALYSIS_STATE_CONFLICT"):
            await service.complete_analysis(
                event_id,
                CallAnalysisResult(classification="TEST", requires_attention=True),
            )
        await service.start_analysis(event_id)
        with pytest.raises(ValueError, match="실패 코드"):
            await service.fail_analysis(event_id, "private-untrusted-error")
        await service.complete_analysis(
            event_id, CallAnalysisResult(classification="TEST", requires_attention=True)
        )
        assert (
            await session.get(SecurityEvent, event_id)
        ).analysis_status == "COMPLETED"
        push.send.assert_not_called()
        legacy = await SecurityEventRepository(session).create(
            family_id, member_id, "ALERT", "CHILD_IMPERSONATION", datetime.now(UTC)
        )
        legacy_id = legacy.id
        await session.commit()
        with pytest.raises(ServiceError, match="EVENT_NOT_CALL"):
            await service.start_analysis(legacy_id)

    runner.run(scenario())
