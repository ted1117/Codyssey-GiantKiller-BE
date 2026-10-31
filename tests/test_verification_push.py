import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.exceptions import ServiceError
from app.models import (
    NotificationRequest,
    SecurityEvent,
    VerificationAnswer,
    VerificationRequest,
)
from app.schemas.push import PushSend
from app.services.push import PushService
from app.services.verification import VerificationService
from tests.test_verification import PREFIX, bearer, prepare_event


def sent(push: AsyncMock) -> list[tuple[UUID, str]]:
    """발송 서비스에 전달된 수신자와 알림 유형을 순서대로 반환합니다."""
    messages: list[PushSend] = [call.args[0] for call in push.send.await_args_list]
    return [(message.user_id, message.message_type.value) for message in messages]


def test_create_sends_after_commit_once(
    engine: AsyncEngine, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """생성 commit 후 자녀 확인 요청과 부모 대기 안내를 한 번만 보냅니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        event_id = event.id
        parent_user_id = parent.user_id
        child_user_ids = [child.user_id for child in children]
        committed: list[int] = []

        async def record(message: PushSend) -> None:
            # 발송 시점에 다른 세션에서 알림 요청이 보여야 commit 이후다.
            async with async_sessionmaker(engine)() as other:
                committed.append(
                    await other.scalar(
                        select(func.count()).select_from(NotificationRequest)
                    )
                )

        push = AsyncMock(spec=PushService)
        push.send.side_effect = record
        request = await VerificationService(session, push).create(event_id)
        assert sorted(sent(push)) == sorted(
            [
                *[(user_id, "VERIFY_REQUEST") for user_id in child_user_ids],
                (parent_user_id, "VERIFY_WAITING"),
            ]
        )
        assert committed == [3, 3, 3]
        messages = [call.args[0] for call in push.send.await_args_list]
        assert {message.event_id for message in messages} == {event_id}
        assert {message.verification_id for message in messages} == {request.id}

        push.send.reset_mock()
        await VerificationService(session, push).create(event_id)
        push.send.assert_not_awaited()

    runner.run(scenario())


def test_create_without_children_sends_expired(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """대상 자녀가 없으면 부모에게 확인 불가 안내만 보냅니다."""

    async def scenario() -> None:
        event, parent, _ = await prepare_event(session, child_count=0)
        parent_user_id = parent.user_id
        push = AsyncMock(spec=PushService)
        await VerificationService(session, push).create(event.id)
        assert sent(push) == [(parent_user_id, "VERIFY_EXPIRED")]

    runner.run(scenario())


def test_rejected_create_sends_nothing(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """롤백된 생성 요청은 알림을 보내지 않습니다."""

    async def scenario() -> None:
        event, _, _ = await prepare_event(session)
        event_id = event.id
        await session.execute(
            update(SecurityEvent)
            .where(SecurityEvent.id == event_id)
            .values(classification="FRAUD")
        )
        await session.commit()
        push = AsyncMock(spec=PushService)
        with pytest.raises(ServiceError):
            await VerificationService(session, push).create(event_id)
        push.send.assert_not_awaited()

    runner.run(scenario())


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (["NOT_SENT", "NOT_SENT"], "VERIFY_DENIED"),
        (["SENT"], "VERIFY_CONFIRMED"),
    ],
)
def test_finalized_response_notifies_parent(
    answers: list[str],
    expected: str,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """결과가 확정된 응답에서만 부모에게 결과 알림을 보냅니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        parent_user_id = parent.user_id
        child_user_ids = [child.user_id for child in children]
        request = await VerificationService(session, AsyncMock()).create(event.id)
        request_id = request.id
        push = AsyncMock(spec=PushService)
        service = VerificationService(session, push)
        for index, answer in enumerate(answers):
            await service.respond(
                request_id, child_user_ids[index], VerificationAnswer(answer)
            )
            if index < len(answers) - 1:
                push.send.assert_not_awaited()
        assert sent(push) == [(parent_user_id, expected)]

    runner.run(scenario())


def test_expiration_notifies_parent(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """주기 만료 작업이 확정한 요청마다 부모에게 확인 불가를 보냅니다."""

    async def scenario() -> None:
        event, parent, _ = await prepare_event(session)
        parent_user_id = parent.user_id
        request = await VerificationService(session, AsyncMock()).create(event.id)
        await session.execute(
            update(VerificationRequest)
            .where(VerificationRequest.id == request.id)
            .values(
                created_at=datetime.now(UTC) - timedelta(minutes=11),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        await session.commit()
        push = AsyncMock(spec=PushService)
        assert await VerificationService(session, push).expire_due() == 1
        assert sent(push) == [(parent_user_id, "VERIFY_EXPIRED")]

    runner.run(scenario())


def test_push_failure_keeps_result(
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """발송 예외는 기록만 하고 확정된 확인 요청을 유지합니다."""

    async def scenario() -> None:
        event, _, _ = await prepare_event(session)
        push = AsyncMock(spec=PushService)
        push.send.side_effect = RuntimeError("fid-secret")
        with caplog.at_level(logging.ERROR):
            request = await VerificationService(session, push).create(event.id)
        assert push.send.await_count == 3
        assert await session.get(VerificationRequest, request.id) is not None
        assert "verification_push_failed" in caplog.text
        assert "secret" not in caplog.text

    runner.run(scenario())


def test_respond_api_uses_injected_push(
    application: FastAPI,
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """응답 API가 주입된 발송 서비스로 부모 결과 알림을 보냅니다."""
    # 설정을 읽는 dependency 모듈은 테스트 설정이 적용된 뒤에 가져온다.
    from app.api.dependencies import get_push_service

    async def scenario() -> None:
        event, parent, children = await prepare_event(session, child_count=1)
        parent_user_id = parent.user_id
        child_user_id = children[0].user_id
        request = await VerificationService(session, AsyncMock()).create(event.id)
        push = application.dependency_overrides[get_push_service]()
        push.send.reset_mock()
        result = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(child_user_id),
            json={"verification_id": str(request.id), "response": "NOT_SENT"},
        )
        assert result.status_code == 200, result.text
        assert sent(push) == [(parent_user_id, "VERIFY_DENIED")]

    runner.run(scenario())
