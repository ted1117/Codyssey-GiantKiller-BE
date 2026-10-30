import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import (
    NotificationRequest,
    NotificationType,
    VerificationAnswer,
    VerificationRequest,
    VerificationStatus,
)
from app.repositories.notification import NotificationRequestRepository
from app.repositories.verification import VerificationRepository
from app.services.verification import VerificationService
from tests.test_verification import PREFIX, bearer, notifications, prepare_event


async def make_due(session: AsyncSession, request: VerificationRequest) -> UUID:
    """기존 요청의 기한을 과거로 옮겨 실제 시간 대기를 피합니다."""
    request_id = request.id
    now = datetime.now(UTC)
    request.created_at = now - timedelta(minutes=11)
    request.expires_at = now - timedelta(minutes=1)
    await session.commit()
    return request_id


def test_expiration_sweep_preserves_other_states(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """일부 응답 요청을 만료시키고 미래·확정 요청은 그대로 둡니다."""

    async def scenario() -> None:
        service = VerificationService(session)
        event, _, children = await prepare_event(session)
        due = await service.create(event.id)
        await service.respond(due.id, children[0].user_id, VerificationAnswer.NOT_SENT)
        due_id = await make_due(session, due)
        event, _, _ = await prepare_event(session)
        pending = await service.create(event.id)
        pending_id = pending.id
        event, _, children = await prepare_event(session)
        confirmed = await service.create(event.id)
        await service.respond(
            confirmed.id, children[0].user_id, VerificationAnswer.SENT
        )
        confirmed_id = await make_due(session, confirmed)
        event, _, children = await prepare_event(session, 1)
        denied = await service.create(event.id)
        await service.respond(
            denied.id, children[0].user_id, VerificationAnswer.NOT_SENT
        )
        denied_id = await make_due(session, denied)
        event, _, _ = await prepare_event(session, 0)
        empty = await service.create(event.id)
        empty_id = empty.id
        assert await service.expire_due() == 1
        assert await service.expire_due() == 0
        repository = VerificationRepository(session)
        assert (await repository.get(due_id)).status == VerificationStatus.EXPIRED
        assert await repository.counts(due_id) == (2, 1, 0)
        for request_id, status in (
            (pending_id, VerificationStatus.PENDING),
            (confirmed_id, VerificationStatus.CONFIRMED),
            (denied_id, VerificationStatus.DENIED),
            (empty_id, VerificationStatus.EXPIRED),
        ):
            assert (await repository.get(request_id)).status == status
        assert (
            sum(
                row.message_type == NotificationType.VERIFY_EXPIRED
                for row in (await notifications(session, due_id))
            )
            == 1
        )

    runner.run(scenario())


def test_expiration_pass_rolls_back_on_notification_failure(
    session: AsyncSession, runner: asyncio.Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 실행의 두 번째 알림 저장 실패도 앞선 상태·알림까지 롤백합니다."""
    original = NotificationRequestRepository.create
    calls = 0

    async def fail_second(
        repository: NotificationRequestRepository,
        event_id: UUID,
        verification_id: UUID,
        user_id: UUID,
        message_type: NotificationType,
    ) -> NotificationRequest:
        """두 번째 알림에서 실제 외래 키 오류를 발생시킵니다."""
        nonlocal calls
        calls += 1
        return await original(
            repository,
            event_id,
            verification_id,
            uuid4() if calls == 2 else user_id,
            message_type,
        )

    async def scenario() -> None:
        service = VerificationService(session)
        request_ids = []
        for _ in range(2):
            event, _, _ = await prepare_event(session)
            request = await service.create(event.id)
            request_ids.append(await make_due(session, request))
        monkeypatch.setattr(NotificationRequestRepository, "create", fail_second)
        with pytest.raises(IntegrityError):
            await service.expire_due()
        for request_id in request_ids:
            assert (
                await VerificationRepository(session).get(request_id)
            ).status == VerificationStatus.PENDING
            assert all(
                row.message_type != NotificationType.VERIFY_EXPIRED
                for row in (await notifications(session, request_id))
            )
        monkeypatch.setattr(NotificationRequestRepository, "create", original)
        assert await service.expire_due() == 2

    runner.run(scenario())


def test_parallel_expiration_passes(
    session: AsyncSession, engine: AsyncEngine, runner: asyncio.Runner
) -> None:
    """여러 서버 작업이 같은 요청을 골라도 만료·알림은 한 번만 확정합니다."""

    async def scenario() -> None:
        event, _, _ = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        request_id = await make_due(session, request)

        async def expire() -> int:
            async with AsyncSession(engine, expire_on_commit=False) as connection:
                return await VerificationService(connection).expire_due()

        assert sorted(await asyncio.gather(expire(), expire())) == [0, 1]
        rows = await notifications(session, request_id)
        assert (
            sum(row.message_type == NotificationType.VERIFY_EXPIRED for row in rows)
            == 1
        )

    runner.run(scenario())


def test_worker_races_with_expired_api_requests(
    client: AsyncClient,
    session: AsyncSession,
    engine: AsyncEngine,
    runner: asyncio.Runner,
) -> None:
    """자동 만료·부모 조회·자녀 응답이 겹쳐도 같은 만료 결과를 유지합니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        request_id = await make_due(session, request)
        async with AsyncSession(engine, expire_on_commit=False) as worker_session:
            expired, viewed, answered = await asyncio.gather(
                VerificationService(worker_session).expire_due(),
                client.get(f"{PREFIX}/{request_id}", headers=bearer(parent.user_id)),
                client.post(
                    f"{PREFIX}/respond",
                    headers=bearer(children[0].user_id),
                    json={"verification_id": str(request_id), "response": "SENT"},
                ),
            )
        assert expired in {0, 1}
        assert viewed.status_code == 200 and viewed.json()["status"] == "EXPIRED"
        assert answered.status_code == 410
        rows = await notifications(session, request_id)
        assert (
            sum(row.message_type == NotificationType.VERIFY_EXPIRED for row in rows)
            == 1
        )
        assert await VerificationRepository(session).counts(request_id) == (2, 0, 0)

    runner.run(scenario())


def test_worker_rechecks_after_waiting_for_valid_response(
    client: AsyncClient,
    session: AsyncSession,
    engine: AsyncEngine,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기한 내 응답의 commit을 기다린 만료 작업이 확정 결과를 덮어쓰지 않습니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        now = request.expires_at - timedelta(microseconds=1)
        response_paused = asyncio.Event()
        release_response = asyncio.Event()
        worker_waiting = asyncio.Event()
        original_create = NotificationRequestRepository.create
        original_get = VerificationRepository.get

        class Clock(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                """응답의 잠금 획득 뒤 시각을 만료 정각으로 진행합니다."""
                return now

        async def pause_confirmation(
            repository: NotificationRequestRepository,
            event_id: UUID,
            verification_id: UUID,
            user_id: UUID,
            message_type: NotificationType,
        ) -> NotificationRequest:
            """유효 응답의 트랜잭션을 commit 직전에 대기시킵니다."""
            if message_type == NotificationType.VERIFY_CONFIRMED:
                response_paused.set()
                await release_response.wait()
            return await original_create(
                repository, event_id, verification_id, user_id, message_type
            )

        async with AsyncSession(engine, expire_on_commit=False) as worker_session:

            async def observe_worker(
                repository: VerificationRepository,
                verification_id: UUID,
                *,
                for_update: bool = False,
            ) -> VerificationRequest | None:
                """주기 작업이 요청 잠금을 기다리는 시점을 관찰합니다."""
                if repository.session is worker_session:
                    worker_waiting.set()
                return await original_get(
                    repository, verification_id, for_update=for_update
                )

            monkeypatch.setattr("app.services.verification.datetime", Clock)
            monkeypatch.setattr(
                NotificationRequestRepository, "create", pause_confirmation
            )
            monkeypatch.setattr(VerificationRepository, "get", observe_worker)
            response_task = asyncio.create_task(
                client.post(
                    f"{PREFIX}/respond",
                    headers=bearer(children[0].user_id),
                    json={"verification_id": str(request.id), "response": "SENT"},
                )
            )
            worker_task = None
            try:
                await asyncio.wait_for(response_paused.wait(), timeout=5)
                now = request.expires_at
                worker_task = asyncio.create_task(
                    VerificationService(worker_session).expire_due()
                )
                await asyncio.wait_for(worker_waiting.wait(), timeout=5)
                release_response.set()
                response = await asyncio.wait_for(response_task, timeout=5)
                assert (
                    response.status_code == 200
                    and response.json()["status"] == "CONFIRMED"
                )
                assert await asyncio.wait_for(worker_task, timeout=5) == 0
            finally:
                release_response.set()
                tasks = [response_task] + (
                    [worker_task] if worker_task is not None else []
                )
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        rows = await notifications(session, request.id)
        assert all(row.message_type != NotificationType.VERIFY_EXPIRED for row in rows)

    runner.run(scenario())


def test_lifespan_startup_repeat_and_shutdown(
    application: FastAPI,
    session: AsyncSession,
    engine: AsyncEngine,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 lifespan이 지난 요청을 복구하고 30초마다 반복한 뒤 작업을 정리합니다."""
    from app import main

    async def scenario() -> None:
        slept: asyncio.Queue[float] = asyncio.Queue()
        resume: asyncio.Queue[None] = asyncio.Queue()

        async def controlled_sleep(delay: float) -> None:
            """실제 30초 대신 테스트 신호로 다음 주기를 진행합니다."""
            await slept.put(delay)
            await resume.get()

        monkeypatch.setattr(
            main,
            "asyncio",
            SimpleNamespace(
                sleep=controlled_sleep,
                create_task=asyncio.create_task,
                CancelledError=asyncio.CancelledError,
            ),
        )
        event, _, _ = await prepare_event(session)
        request_id = await make_due(
            session, await VerificationService(session).create(event.id)
        )
        async with application.router.lifespan_context(application):
            assert await asyncio.wait_for(slept.get(), timeout=5) == 30
            worker = next(
                task
                for task in asyncio.all_tasks()
                if task.get_name() == "verification-expiration"
            )
            session.expire_all()
            assert (
                await VerificationRepository(session).get(request_id)
            ).status == VerificationStatus.EXPIRED
            await session.commit()
            event, _, _ = await prepare_event(session)
            next_id = await make_due(
                session, await VerificationService(session).create(event.id)
            )
            await resume.put(None)
            assert await asyncio.wait_for(slept.get(), timeout=5) == 30
            session.expire_all()
            assert (
                await VerificationRepository(session).get(next_id)
            ).status == VerificationStatus.EXPIRED
            await session.commit()
        assert worker.done() and worker.cancelled()
        assert not any(
            task.get_name() == "verification-expiration" for task in asyncio.all_tasks()
        )
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1

    runner.run(asyncio.wait_for(scenario(), timeout=15))


def test_lifespan_worker_recovers_from_database_error(
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB 오류를 롤백하고 다음 주기에 재처리하며 SQL 원문은 로그에서 제외합니다."""
    from app import main

    original = NotificationRequestRepository.create
    failed = False

    async def fail_once(
        repository: NotificationRequestRepository,
        event_id: UUID,
        verification_id: UUID,
        user_id: UUID,
        message_type: NotificationType,
    ) -> NotificationRequest:
        """첫 발송 의도 저장에만 없는 사용자를 참조합니다."""
        nonlocal failed
        invalid_user = not failed
        failed = True
        return await original(
            repository,
            event_id,
            verification_id,
            uuid4() if invalid_user else user_id,
            message_type,
        )

    async def scenario() -> None:
        slept: asyncio.Queue[float] = asyncio.Queue()
        resume: asyncio.Queue[None] = asyncio.Queue()

        async def controlled_sleep(delay: float) -> None:
            """오류 다음 주기를 테스트 신호로 진행합니다."""
            await slept.put(delay)
            await resume.get()

        event, _, _ = await prepare_event(session)
        request_id = await make_due(
            session, await VerificationService(session).create(event.id)
        )
        monkeypatch.setattr(NotificationRequestRepository, "create", fail_once)
        monkeypatch.setattr(
            main,
            "asyncio",
            SimpleNamespace(
                sleep=controlled_sleep,
                create_task=asyncio.create_task,
                CancelledError=asyncio.CancelledError,
            ),
        )
        with caplog.at_level(logging.ERROR, logger="app.main"):
            async with application.router.lifespan_context(application):
                assert await asyncio.wait_for(slept.get(), timeout=5) == 30
                session.expire_all()
                assert (
                    await VerificationRepository(session).get(request_id)
                ).status == VerificationStatus.PENDING
                await session.commit()
                await resume.put(None)
                assert await asyncio.wait_for(slept.get(), timeout=5) == 30
                session.expire_all()
                assert (
                    await VerificationRepository(session).get(request_id)
                ).status == VerificationStatus.EXPIRED
        assert "verification_expiration_failed error_type=IntegrityError" in caplog.text
        assert (
            "INSERT INTO" not in caplog.text
            and "원문 대신 사건 요약" not in caplog.text
        )
        rows = await notifications(session, request_id)
        assert (
            sum(row.message_type == NotificationType.VERIFY_EXPIRED for row in rows)
            == 1
        )

    runner.run(asyncio.wait_for(scenario(), timeout=15))


def test_shutdown_cancels_worker_waiting_for_database_lock(
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 잠금 대기 중에도 종료 시 작업이 취소되어 만료 부분 쓰기가 남지 않습니다."""

    async def scenario() -> None:
        event, _, _ = await prepare_event(session)
        request_id = await make_due(
            session, await VerificationService(session).create(event.id)
        )
        repository = VerificationRepository(session)
        await repository.get(request_id, for_update=True)
        attempted = asyncio.Event()
        original = VerificationRepository.get

        async def observed_get(
            other: VerificationRepository,
            verification_id: UUID,
            *,
            for_update: bool = False,
        ) -> VerificationRequest | None:
            """주기 작업의 행 잠금 시도를 관찰합니다."""
            if other.session is not session:
                attempted.set()
            return await original(other, verification_id, for_update=for_update)

        monkeypatch.setattr(VerificationRepository, "get", observed_get)
        async with application.router.lifespan_context(application):
            await asyncio.wait_for(attempted.wait(), timeout=5)
            worker = next(
                task
                for task in asyncio.all_tasks()
                if task.get_name() == "verification-expiration"
            )
            assert not worker.done()
        assert worker.done() and worker.cancelled()
        await session.rollback()
        assert (await repository.get(request_id)).status == VerificationStatus.PENDING

    runner.run(asyncio.wait_for(scenario(), timeout=15))
