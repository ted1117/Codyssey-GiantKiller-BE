import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from threading import Event, get_ident
from uuid import uuid4

import pytest
from firebase_admin import exceptions, messaging
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.fcm import FCMClient
from app.models import PushRegistration, SecurityEvent, User
from app.repositories.push_registration import PushRegistrationRepository
from app.schemas.push import PushMessageType, PushSend
from app.services.push import PushService
from app.services.verification import VerificationService
from tests.test_membership import add_user
from tests.test_verification import notifications, prepare_event


class FakeFCM(FCMClient):
    """실제 통신 없이 FID별 결과와 발송 중 경합을 재현합니다."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, str, dict[str, str]]] = []
        self.errors: dict[str, Exception] = {}
        self.failure: Exception | None = None
        self.during_send: Callable[[], Awaitable[None]] | None = None

    async def send(
        self, fids: list[str], title: str, body: str, data: dict[str, str]
    ) -> messaging.BatchResponse:
        self.calls.append((fids, title, body, data))
        if self.during_send is not None:
            await self.during_send()
        if self.failure is not None:
            raise self.failure
        return messaging.BatchResponse(
            [
                messaging.SendResponse(
                    None if fid in self.errors else {"name": "message-id"},
                    self.errors.get(fid),
                )
                for fid in fids
            ]
        )


@pytest.mark.parametrize("kind", list(PushMessageType))
def test_message_payloads(
    kind: PushMessageType,
    engine: AsyncEngine,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """모든 유형의 표시 문구·ID·문자열 액션을 확인합니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id, event_id = user.id, uuid4()
        await PushRegistrationRepository(session).upsert(user_id, "private-fid")
        await session.commit()
        client = FakeFCM()
        verification_id = uuid4() if kind.value.startswith("VERIFY_") else None
        result = await PushService(async_sessionmaker(engine), client).send(
            PushSend(
                user_id=user_id,
                event_id=event_id,
                message_type=kind,
                verification_id=verification_id,
            )
        )
        assert result.success_count == 1 and result.failure_count == 0
        fids, title, body, data = client.calls[0]
        assert fids == ["private-fid"] and title and body
        assert data["event_id"] == str(event_id) and data["push_type"] == kind.value
        assert "message_type" not in data
        assert all(isinstance(value, str) for value in data.values())
        assert ("verification_id" in data) == (verification_id is not None)
        assert ("actions" in data) == (kind == PushMessageType.VERIFY_REQUEST)
        if "actions" in data:
            assert json.loads(data["actions"]) == ["SENT", "NOT_SENT"]
        if kind == PushMessageType.VERIFY_CONFIRMED:
            assert "보장하는 것은 아니" in body
        if kind == PushMessageType.VERIFY_DENIED:
            assert "사칭 확정" not in title + body
        assert "private-fid" not in json.dumps(data)

    runner.run(scenario())


FCM_RESERVED_KEYS = {"message_type", "from", "notification", "collapse_key"}


@pytest.mark.parametrize("kind", list(PushMessageType))
def test_data_keys_avoid_fcm_reserved(
    kind: PushMessageType,
    engine: AsyncEngine,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """FCM data 키가 예약 키와 겹치지 않는지 모든 유형에서 확인합니다."""

    async def scenario() -> None:
        user = await add_user(session)
        await PushRegistrationRepository(session).upsert(user.id, "private-fid")
        await session.commit()
        client = FakeFCM()
        verification_id = uuid4() if kind.value.startswith("VERIFY_") else None
        await PushService(async_sessionmaker(engine), client).send(
            PushSend(
                user_id=user.id,
                event_id=uuid4(),
                message_type=kind,
                verification_id=verification_id,
            )
        )
        data = client.calls[0][3]
        assert not FCM_RESERVED_KEYS & set(data)
        assert not any(key.startswith(("google.", "gcm.")) for key in data)

    runner.run(scenario())


def test_verification_payload_requires_id() -> None:
    """확인 알림의 ID 누락과 일반 알림의 잘못된 확인 ID를 거부합니다."""
    for kind in PushMessageType:
        fields = {"user_id": uuid4(), "event_id": uuid4(), "message_type": kind}
        if not kind.value.startswith("VERIFY_"):
            fields["verification_id"] = uuid4()
        with pytest.raises(ValidationError):
            PushSend(**fields)


def test_partial_failure_preserves_event_and_private_logs(
    engine: AsyncEngine,
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """일부 실패는 사건을 유지하고 명확한 무효 FID만 삭제합니다."""

    async def scenario() -> None:
        event, parent, _ = await prepare_event(session)
        event_id, user_id = event.id, parent.user_id
        repository = PushRegistrationRepository(session)
        for fid in (
            "success-secret",
            "unregistered-secret",
            "invalid-argument-secret",
        ):
            await repository.upsert(user_id, fid)
        await session.commit()
        client = FakeFCM()
        client.errors = {
            "unregistered-secret": messaging.UnregisteredError("unregistered-secret"),
            "invalid-argument-secret": exceptions.InvalidArgumentError(
                "payload-secret"
            ),
        }
        with caplog.at_level(logging.INFO, logger="app.services.push"):
            result = await PushService(async_sessionmaker(engine), client).send(
                PushSend(
                    user_id=user_id, event_id=event_id, message_type="RISK_WARNING"
                )
            )
        assert (result.success_count, result.failure_count) == (1, 2)
        assert len(client.calls) == 1
        rows = await repository.list_by_user(user_id)
        assert {r.fid for r in rows} == {"success-secret", "invalid-argument-secret"}
        assert (await session.get(SecurityEvent, event_id)).status == "OPEN"
        assert "push_sent" in caplog.text and "push_failed" in caplog.text
        assert "secret" not in caplog.text

    runner.run(scenario())


@pytest.mark.parametrize("reassign", [False, True])
def test_network_phase_has_no_db_transaction_and_cleanup_is_conditional(
    reassign: bool,
    engine: AsyncEngine,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """통신 시 DB 연결을 놓고 재등록·계정 전환된 FID을 정리하지 않습니다."""

    async def scenario() -> None:
        first, second = await add_user(session), await add_user(session)
        first_id, second_id = first.id, second.id
        await PushRegistrationRepository(session).upsert(first_id, "changing")
        await session.commit()
        sessions = async_sessionmaker(engine)
        opened: list[AsyncSession] = []

        class ObservedSession(AsyncSession):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                opened.append(self)

        client = FakeFCM()
        client.errors["changing"] = messaging.UnregisteredError("secret")

        async def change() -> None:
            assert all(not item.in_transaction() for item in opened)
            async with sessions() as connection:
                await PushRegistrationRepository(connection).upsert(
                    second_id if reassign else first_id, "changing"
                )
                await connection.commit()

        client.during_send = change
        result = await PushService(
            async_sessionmaker(engine, class_=ObservedSession), client
        ).send(
            PushSend(user_id=first_id, event_id=uuid4(), message_type="RISK_WARNING")
        )
        assert result.failure_count == 1
        rows = await PushRegistrationRepository(session).list_by_user(
            second_id if reassign else first_id
        )
        assert [r.fid for r in rows] == ["changing"]

    runner.run(scenario())


def test_initialization_failure_is_logged_without_retry(
    engine: AsyncEngine,
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """자격 증명 오류가 FID 삭제나 재시도로 이어지지 않습니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        for fid in ("one", "two"):
            await PushRegistrationRepository(session).upsert(user_id, fid)
        await session.commit()
        client = FakeFCM()
        client.failure = ValueError("credential-secret")
        result = await PushService(async_sessionmaker(engine), client).send(
            PushSend(user_id=user_id, event_id=uuid4(), message_type="CALL_SUMMARY")
        )
        assert (result.success_count, result.failure_count) == (0, 2)
        assert len(client.calls) == 1
        assert len(await PushRegistrationRepository(session).list_by_user(user_id)) == 2
        assert "ValueError" in caplog.text and "credential-secret" not in caplog.text

    runner.run(scenario())


def test_skipped_users_and_unconnected_verification(
    engine: AsyncEngine, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """FID 없음·비활성 계정은 건너뛰고 기존 확인 요청은 자동 발송하지 않습니다."""

    async def scenario() -> None:
        event, parent, _ = await prepare_event(session)
        event_id, user_id = event.id, parent.user_id
        client = FakeFCM()
        service = PushService(async_sessionmaker(engine), client)
        message = PushSend(
            user_id=user_id, event_id=event_id, message_type="RISK_WARNING"
        )
        assert (await service.send(message)).skipped_reason == "NO_REGISTRATIONS"
        await PushRegistrationRepository(session).upsert(user_id, "parent-fid")
        request = await VerificationService(session).create(event_id)
        assert await notifications(session, request.id)
        assert client.calls == []
        user = await session.get(User, user_id)
        user.is_active = False
        await session.commit()
        assert (
            await service.send(message)
        ).skipped_reason == "USER_INACTIVE_OR_MISSING"
        message.user_id = uuid4()
        assert (
            await service.send(message)
        ).skipped_reason == "USER_INACTIVE_OR_MISSING"
        assert client.calls == []

    runner.run(scenario())


def test_batches_and_cleanup_failure_preserve_success(
    engine: AsyncEngine,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """500건씩 발송하고 정리 DB 오류가 성공 건의 결과를 바꾸지 않습니다."""

    async def scenario() -> None:
        user = await add_user(session)
        user_id = user.id
        await session.execute(
            PushRegistration.__table__.insert(),
            [{"user_id": user_id, "fid": f"fid-{i}"} for i in range(501)],
        )
        await session.commit()
        client = FakeFCM()
        client.errors["fid-0"] = messaging.UnregisteredError("fid-secret")

        async def fail(*args: object) -> None:
            raise SQLAlchemyError("sql-secret")

        monkeypatch.setattr(PushRegistrationRepository, "delete_if_unchanged", fail)
        result = await PushService(async_sessionmaker(engine), client).send(
            PushSend(user_id=user_id, event_id=uuid4(), message_type="RISK_WARNING")
        )
        assert [len(call[0]) for call in client.calls] == [500, 1]
        assert (result.success_count, result.failure_count) == (500, 1)
        assert len(list(await session.scalars(select(PushRegistration)))) == 501
        assert "push_cleanup_failed" in caplog.text and "secret" not in caplog.text

    runner.run(scenario())


@pytest.mark.filterwarnings("error::DeprecationWarning")
def test_sdk_adapter_uses_notification_and_data_in_worker(
    runner: asyncio.Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실제 SDK 메시지 객체가 FID·표시 문구·데이터를 함께 전달합니다."""
    app = object()
    monkeypatch.setattr("app.core.fcm._get_app", lambda: app)

    def send(
        messages: list[messaging.Message], **kwargs: object
    ) -> messaging.BatchResponse:
        assert kwargs["app"] is app
        assert [message.fid for message in messages] == ["one", "two"]
        for message in messages:
            payload = json.loads(str(message))
            assert payload["fid"] == message.fid
            assert "token" not in payload
            assert message.notification.title == "제목"
            assert message.notification.body == "안내"
            assert message.data == {"event_id": "event"}
        return messaging.BatchResponse(
            [messaging.SendResponse({"name": "id"}, None) for _ in messages]
        )

    monkeypatch.setattr(messaging, "send_each", send)
    result = runner.run(
        FCMClient().send(["one", "two"], "제목", "안내", {"event_id": "event"})
    )
    assert result.success_count == 2


@pytest.mark.parametrize("phase", ["initialization", "authenticated_send"])
def test_sdk_blocking_io_does_not_block_event_loop(
    phase: str, runner: asyncio.Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """최초 초기화와 인증을 포함한 발송이 대기해도 다른 async 작업은 진행합니다."""
    entered, release = Event(), Event()
    loop_thread = get_ident()
    app = object()

    def block() -> None:
        assert get_ident() != loop_thread
        entered.set()
        assert release.wait(timeout=5)

    def get_app() -> object:
        assert get_ident() != loop_thread
        if phase == "initialization":
            block()
        return app

    def send(
        messages: list[messaging.Message], **kwargs: object
    ) -> messaging.BatchResponse:
        assert get_ident() != loop_thread
        assert kwargs["app"] is app
        if phase == "authenticated_send":
            block()
        return messaging.BatchResponse(
            [messaging.SendResponse({"name": "id"}, None) for _ in messages]
        )

    monkeypatch.setattr("app.core.fcm._get_app", get_app)
    monkeypatch.setattr(messaging, "send_each", send)

    async def scenario() -> None:
        task = asyncio.create_task(FCMClient().send(["fid"], "제목", "안내", {}))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            # worker가 해제 신호를 기다리는 동안 이벤트 루프에서 실행한다.
            assert not task.done()
        finally:
            release.set()
            result = await asyncio.wait_for(task, timeout=5)
        assert result.success_count == 1

    runner.run(scenario())
