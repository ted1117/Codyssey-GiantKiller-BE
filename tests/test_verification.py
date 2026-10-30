import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.exceptions import ServiceError
from app.models import (
    FamilyMember,
    FamilyRole,
    NotificationRequest,
    NotificationType,
    SecurityEvent,
    VerificationAnswer,
    VerificationRequest,
    VerificationResponse,
    VerificationStatus,
)
from app.repositories.event import SecurityEventRepository
from app.repositories.family import FamilyMemberRepository, FamilyRepository
from app.repositories.notification import NotificationRequestRepository
from app.repositories.verification import VerificationRepository
from app.services.verification import VerificationService
from tests.test_api import account, family, invitation
from tests.test_membership import add_user

PREFIX = "/api/v1/verify"


def bearer(user_id: UUID) -> dict[str, str]:
    """테스트 설정의 서명 키로 실제 인증을 통과하는 JWT를 만듭니다."""
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": ["fastapi-users:auth"],
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        "stage3-test-secret-" * 3,
    )
    return {"Authorization": f"Bearer {token}"}


async def prepare_event(
    session: AsyncSession, child_count: int = 2
) -> tuple[SecurityEvent, FamilyMember, list[FamilyMember]]:
    """실제 계정·가족·부모·자녀와 사칭 사건을 테스트 DB에 저장합니다."""
    group = await FamilyRepository(session).create()
    members = FamilyMemberRepository(session)
    parent_user = await add_user(session, "부모")
    parent = await members.create(group.id, parent_user.id, FamilyRole.PARENT)
    children = []
    for index in range(child_count):
        user = await add_user(session, f"자녀 {index}")
        children.append(
            await members.create(
                group.id, user.id, FamilyRole.CHILD, is_representative=index == 0
            )
        )
    event = await SecurityEventRepository(session).create(
        group.id,
        parent.id,
        "ALERT",
        "CHILD_IMPERSONATION",
        datetime.now(UTC),
        summary="원문 대신 사건 요약",
    )
    await session.commit()
    return event, parent, children


async def notifications(
    session: AsyncSession, verification_id: UUID
) -> list[NotificationRequest]:
    """해당 요청의 발송 의도를 테스트에서 직접 확인합니다."""
    return list(
        await session.scalars(
            select(NotificationRequest).where(
                NotificationRequest.verification_id == verification_id
            )
        )
    )


def test_registered_family_api_end_to_end(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """실제 가입·초대 이후 내부 생성과 자녀 응답·부모 조회가 연결됩니다."""

    async def scenario() -> None:
        child_id, child_headers = await account(client)
        parent_id, parent_headers = await account(client)
        family_id = await family(client, child_headers)
        issued = await invitation(client, family_id, child_headers)
        assert (
            await client.post(f"{issued['path']}/accept", headers=parent_headers)
        ).status_code == 200
        parent = await FamilyMemberRepository(session).get(
            UUID(family_id), UUID(parent_id)
        )
        event = await SecurityEventRepository(session).create(
            UUID(family_id),
            parent.id,
            "ALERT",
            "CHILD_IMPERSONATION",
            datetime.now(UTC),
        )
        await session.commit()
        request = await VerificationService(session).create(event.id)
        result = await client.post(
            f"{PREFIX}/respond",
            headers=child_headers,
            json={"verification_id": str(request.id), "response": "SENT"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "CONFIRMED"
        assert result.json()["my_response"] == "SENT"
        assert str(child_id) not in result.text
        viewed = await client.get(f"{PREFIX}/{request.id}", headers=parent_headers)
        assert viewed.status_code == 200
        assert viewed.json()["my_response"] is None
        assert viewed.json()["status"] == "CONFIRMED"

    runner.run(scenario())


def test_all_denials_and_personal_results(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """일부 부인은 대기하고 마지막 부인만 확정하며 내 응답만 노출합니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        assert request.expires_at - request.created_at == timedelta(minutes=10)
        rows = await notifications(session, request.id)
        assert {(row.user_id, row.message_type) for row in rows} == {
            (children[0].user_id, NotificationType.VERIFY_REQUEST),
            (children[1].user_id, NotificationType.VERIFY_REQUEST),
            (parent.user_id, NotificationType.VERIFY_WAITING),
        }
        await session.commit()
        first = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(children[0].user_id),
            json={"verification_id": str(request.id), "response": "NOT_SENT"},
        )
        assert first.status_code == 200
        assert first.json() == {
            "id": str(request.id),
            "event_id": str(event.id),
            "status": "PENDING",
            "expires_at": request.expires_at.isoformat().replace("+00:00", "Z"),
            "target_count": 2,
            "responded_count": 1,
            "my_response": "NOT_SENT",
        }
        other = await client.get(
            f"{PREFIX}/{request.id}", headers=bearer(children[1].user_id)
        )
        assert other.json()["my_response"] is None
        last = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(children[1].user_id),
            json={"verification_id": str(request.id), "response": "NOT_SENT"},
        )
        assert last.status_code == 200 and last.json()["status"] == "DENIED"
        assert last.json()["responded_count"] == 2
        rows = await notifications(session, request.id)
        assert (
            sum(row.message_type == NotificationType.VERIFY_DENIED for row in rows) == 1
        )
        assert (await session.get(SecurityEvent, event.id)).status == "OPEN"

        repeated = await VerificationService(session).create(event.id)
        assert (
            repeated.id == request.id and repeated.status == VerificationStatus.DENIED
        )
        assert len(await notifications(session, repeated.id)) == 4

    runner.run(scenario())


def test_one_sent_confirms_without_every_response(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """한 자녀의 발송 확인으로 종료하고 나머지 응답은 결과를 바꾸지 않습니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session, 3)
        request = await VerificationService(session).create(event.id)
        response = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(children[0].user_id),
            json={"verification_id": str(request.id), "response": "SENT"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "CONFIRMED"
        assert response.json()["target_count"] == 3
        assert response.json()["responded_count"] == 1
        rejected = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(children[1].user_id),
            json={"verification_id": str(request.id), "response": "NOT_SENT"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "VERIFICATION_FINALIZED"
        rows = await notifications(session, request.id)
        assert [
            (row.user_id, row.message_type)
            for row in rows
            if (row.message_type == NotificationType.VERIFY_CONFIRMED)
        ] == [(parent.user_id, NotificationType.VERIFY_CONFIRMED)]
        assert (await session.get(SecurityEvent, event.id)).status == "OPEN"

    runner.run(scenario())


def test_duplicate_and_non_target_responses(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """부모·사후 가입 자녀·타 가족은 차단하고 개인 중복 응답도 거부합니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        late_user = await add_user(session, "나중 참여")
        await FamilyMemberRepository(session).create(
            event.family_id, late_user.id, FamilyRole.CHILD
        )
        outsider = await add_user(session, "타 가족")
        await session.commit()
        body = {"verification_id": str(request.id), "response": "NOT_SENT"}
        for user_id, code in (
            (parent.user_id, "VERIFICATION_RESPONDER_REQUIRED"),
            (late_user.id, "VERIFICATION_RESPONDER_REQUIRED"),
            (outsider.id, "FAMILY_ACCESS_DENIED"),
        ):
            response = await client.post(
                f"{PREFIX}/respond", headers=bearer(user_id), json=body
            )
            assert response.status_code == 403 and response.json()["detail"] == code
        assert (
            await client.get(f"{PREFIX}/{request.id}", headers=bearer(outsider.id))
        ).status_code == 403
        headers = bearer(children[0].user_id)
        assert (
            await client.post(f"{PREFIX}/respond", headers=headers, json=body)
        ).status_code == 200
        for answer in ("NOT_SENT", "SENT"):
            response = await client.post(
                f"{PREFIX}/respond", headers=headers, json=body | {"response": answer}
            )
            assert response.status_code == 409
            assert response.json()["detail"] == "VERIFICATION_ALREADY_RESPONDED"
        assert await VerificationRepository(session).counts(request.id) == (2, 1, 0)

    runner.run(scenario())


def test_zero_children_expires_immediately(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """빈 대상은 발송 부인이 아닌 즉시 확인 불가이며 부모 알림만 생성합니다."""

    async def scenario() -> None:
        event, parent, _ = await prepare_event(session, 0)
        request = await VerificationService(session).create(event.id)
        assert request.status == VerificationStatus.EXPIRED
        assert request.created_at == request.expires_at == request.finalized_at
        response = await client.get(
            f"{PREFIX}/{request.id}", headers=bearer(parent.user_id)
        )
        assert response.status_code == 200
        assert (
            response.json()["target_count"] == response.json()["responded_count"] == 0
        )
        assert response.json()["status"] == "EXPIRED"
        rows = await notifications(session, request.id)
        assert (
            len(rows) == 1 and rows[0].message_type == NotificationType.VERIFY_EXPIRED
        )
        assert rows[0].user_id == parent.user_id

    runner.run(scenario())


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_exact_deadline_and_expiry_commit(
    offset: int,
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기한 직전만 응답 가능하며 정각 이후 410에도 만료·알림 저장이 유지됩니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        now = request.expires_at + timedelta(microseconds=offset)
        request_id, child_user_id = request.id, children[0].user_id

        class Clock(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                """검증할 기한 경계 시각을 반환합니다."""
                return now

        monkeypatch.setattr("app.services.verification.datetime", Clock)
        response = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(children[0].user_id),
            json={"verification_id": str(request.id), "response": "NOT_SENT"},
        )
        if offset < 0:
            assert (
                response.status_code == 200 and response.json()["status"] == "PENDING"
            )
        else:
            assert response.status_code == 410
            session.expire_all()
            stored = await VerificationRepository(session).get(request_id)
            assert (
                stored.status == VerificationStatus.EXPIRED
                and stored.finalized_at == now
            )
            assert await VerificationRepository(session).counts(stored.id) == (2, 0, 0)
            for _ in range(2):
                viewed = await client.get(
                    f"{PREFIX}/{stored.id}", headers=bearer(child_user_id)
                )
                assert (
                    viewed.status_code == 200 and viewed.json()["status"] == "EXPIRED"
                )
            rows = await notifications(session, stored.id)
            assert (
                sum(row.message_type == NotificationType.VERIFY_EXPIRED for row in rows)
                == 1
            )

    runner.run(scenario())


def test_expired_view_and_unauthorized_calls(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """권한 없는 만료 요청 접근은 상태를 바꾸지 않고 부모 조회가 만료시킵니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        request.created_at = datetime.now(UTC) - timedelta(minutes=11)
        request.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        outsider = await add_user(session)
        await session.commit()
        request_id = request.id
        parent_user_id, child_user_id = parent.user_id, children[0].user_id
        for headers in (bearer(outsider.id), {}):
            response = await client.get(f"{PREFIX}/{request_id}", headers=headers)
            assert response.status_code in {401, 403}
        session.expire_all()
        assert (
            await VerificationRepository(session).get(request_id)
        ).status == VerificationStatus.PENDING
        await session.commit()
        response = await client.get(
            f"{PREFIX}/{request_id}", headers=bearer(parent_user_id)
        )
        assert response.status_code == 200 and response.json()["status"] == "EXPIRED"
        rejected = await client.post(
            f"{PREFIX}/respond",
            headers=bearer(child_user_id),
            json={"verification_id": str(request_id), "response": "SENT"},
        )
        assert rejected.status_code == 410

    runner.run(scenario())


@pytest.mark.parametrize(
    "failure", ["missing", "classification", "parent_role", "parent_family"]
)
def test_invalid_event_rejected_without_partial_writes(
    failure: str,
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """없는 사건·대상 외 분류·부모 정보 오류는 요청과 알림을 만들지 않습니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        code = "EVENT_NOT_FOUND" if failure == "missing" else "EVENT_PARENT_MISMATCH"
        if failure == "classification":
            event.classification = "OTHER"
            code = "EVENT_NOT_VERIFIABLE"
        elif failure == "parent_role":
            event.parent_member_id = children[0].id
        elif failure == "parent_family":
            other, parent, _ = await prepare_event(session)
            event.parent_member_id = parent.id
            assert event.family_id != other.family_id
        await session.commit()
        event_id = uuid4() if failure == "missing" else event.id
        with pytest.raises(ServiceError) as error:
            await VerificationService(session).create(event_id)
        assert error.value.detail == code
        assert (
            await session.scalar(select(func.count()).select_from(VerificationRequest))
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(NotificationRequest))
            == 0
        )
        assert code in caplog.text and "원문 대신 사건 요약" not in caplog.text

    runner.run(scenario())


def test_creation_repeat_and_concurrency(
    session: AsyncSession, engine: AsyncEngine, runner: asyncio.Runner
) -> None:
    """동시 생성에서도 한 요청·대상 집합만 만들고 기한을 초기화하지 않습니다."""

    async def scenario() -> None:
        event, _, _ = await prepare_event(session)

        async def create() -> tuple[UUID, datetime]:
            async with AsyncSession(engine, expire_on_commit=False) as connection:
                request = await VerificationService(connection).create(event.id)
                return request.id, request.expires_at

        first, second = await asyncio.gather(create(), create())
        assert first == second
        repeat = await VerificationService(session).create(event.id)
        assert (repeat.id, repeat.expires_at) == first
        assert (
            await session.scalar(select(func.count()).select_from(VerificationRequest))
            == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(VerificationResponse))
            == 2
        )
        assert len(await notifications(session, repeat.id)) == 3

    runner.run(scenario())


@pytest.mark.parametrize(
    "answers", [("NOT_SENT", "NOT_SENT"), ("NOT_SENT", "SENT"), ("SENT", "SENT")]
)
def test_concurrent_children(
    answers: tuple[str, str],
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """다른 자녀의 동시 응답은 하나의 일관된 최종 결과와 알림만 만듭니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        results = await asyncio.gather(
            *(
                client.post(
                    f"{PREFIX}/respond",
                    headers=bearer(child.user_id),
                    json={"verification_id": str(request.id), "response": answer},
                )
                for child, answer in zip(children, answers, strict=True)
            )
        )
        assert all(result.status_code in {200, 409} for result in results)
        request_id = request.id
        session.expire_all()
        stored = await VerificationRepository(session).get(request_id)
        expected = (
            VerificationStatus.DENIED
            if "SENT" not in answers
            else VerificationStatus.CONFIRMED
        )
        assert stored.status == expected
        rows = await notifications(session, stored.id)
        assert (
            sum(
                row.message_type
                in {NotificationType.VERIFY_CONFIRMED, NotificationType.VERIFY_DENIED}
                for row in rows
            )
            == 1
        )

    runner.run(scenario())


def test_concurrent_duplicate_answer(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """동일 자녀의 동시 응답은 하나만 기록합니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        results = await asyncio.gather(
            *(
                client.post(
                    f"{PREFIX}/respond",
                    headers=bearer(children[0].user_id),
                    json={"verification_id": str(request.id), "response": "NOT_SENT"},
                )
                for _ in range(2)
            )
        )
        assert sorted(result.status_code for result in results) == [200, 409]
        assert await VerificationRepository(session).counts(request.id) == (2, 1, 0)

    runner.run(scenario())


@pytest.mark.parametrize("operation", ["create", "respond", "expire"])
def test_notification_failure_rolls_back_service_operation(
    operation: str,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """알림 저장 중 오류가 생기면 요청·대상·응답·확정의 부분 쓰기를 막습니다."""

    async def fail(*args: object, **kwargs: object) -> None:
        """알림 저장 실패를 재현합니다."""
        raise RuntimeError("notification failed")

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        service = VerificationService(session)
        request_id = None
        if operation != "create":
            request = await service.create(event.id)
            request_id = request.id
            if operation == "expire":
                request.created_at = datetime.now(UTC) - timedelta(minutes=11)
                request.expires_at = datetime.now(UTC) - timedelta(minutes=1)
                await session.commit()
        event_id, child_user_id = event.id, children[0].user_id
        monkeypatch.setattr(NotificationRequestRepository, "create", fail)
        with pytest.raises(RuntimeError, match="notification failed"):
            if operation == "create":
                await service.create(event_id)
            elif operation == "respond":
                await service.respond(
                    request_id, child_user_id, VerificationAnswer.SENT
                )
            else:
                await service.get_detail(request_id, child_user_id)
        if operation == "create":
            assert await VerificationRepository(session).get_by_event(event_id) is None
            assert (
                await session.scalar(
                    select(func.count()).select_from(VerificationResponse)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(NotificationRequest)
                )
                == 0
            )
        else:
            stored = await VerificationRepository(session).get(request_id)
            assert (
                stored.status == VerificationStatus.PENDING
                and stored.finalized_at is None
            )
            assert await VerificationRepository(session).counts(request_id) == (2, 0, 0)

    runner.run(scenario())


def test_api_auth_validation_and_missing_request(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """인증·입력·없는 요청 오류를 API로 검증하고 원문이 로그에 남지 않습니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        headers = bearer(children[0].user_id)
        body = {"verification_id": str(request.id), "response": "SENT"}
        for auth in ({}, {"Authorization": "Bearer invalid"}):
            assert (
                await client.post(f"{PREFIX}/respond", headers=auth, json=body)
            ).status_code == 401
            assert (
                await client.get(f"{PREFIX}/{request.id}", headers=auth)
            ).status_code == 401
        for change in (
            {"response": "SAFE"},
            {"user_id": str(children[1].user_id)},
            {"verification_id": "bad"},
        ):
            assert (
                await client.post(
                    f"{PREFIX}/respond", headers=headers, json=body | change
                )
            ).status_code == 422
        assert (await client.get(f"{PREFIX}/bad", headers=headers)).status_code == 422
        for response in (
            await client.get(f"{PREFIX}/{uuid4()}", headers=headers),
            await client.post(
                f"{PREFIX}/respond",
                headers=headers,
                json=body | {"verification_id": str(uuid4())},
            ),
        ):
            assert response.status_code == 404
            assert response.json()["detail"] == "VERIFICATION_NOT_FOUND"
        with caplog.at_level(logging.INFO, logger="app.services.verification"):
            response = await client.post(
                f"{PREFIX}/respond", headers=headers, json=body
            )
        assert response.status_code == 200
        assert "verification_responded" in caplog.text
        assert "원문 대신 사건 요약" not in caplog.text
        assert headers["Authorization"] not in caplog.text
        assert "summary" not in response.json()

    runner.run(scenario())


@pytest.mark.parametrize(
    "answer", [VerificationAnswer.SENT, VerificationAnswer.NOT_SENT]
)
def test_single_child_and_finalized_result_after_deadline(
    answer: VerificationAnswer,
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 자녀의 부인도 전원 부인이며 확정 결과는 기한 뒤에도 유지됩니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session, 1)
        request = await VerificationService(session).create(event.id)
        headers = bearer(children[0].user_id)
        body = {"verification_id": str(request.id), "response": answer.value}
        expected = "CONFIRMED" if answer == VerificationAnswer.SENT else "DENIED"
        response = await client.post(f"{PREFIX}/respond", headers=headers, json=body)
        assert response.status_code == 200 and response.json()["status"] == expected
        late = request.expires_at + timedelta(seconds=1)

        class Clock(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                """확정 요청의 기한 이후 시각을 반환합니다."""
                return late

        monkeypatch.setattr("app.services.verification.datetime", Clock)
        viewed = await client.get(f"{PREFIX}/{request.id}", headers=headers)
        assert viewed.status_code == 200 and viewed.json()["status"] == expected
        rejected = await client.post(f"{PREFIX}/respond", headers=headers, json=body)
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "VERIFICATION_FINALIZED"
        assert all(
            row.message_type != NotificationType.VERIFY_EXPIRED
            for row in (await notifications(session, request.id))
        )

    runner.run(scenario())


def test_duplicate_answer_after_expiry_returns_gone(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대기 중 개인 응답이 있어도 만료 정각부터는 중복보다 만료 오류가 우선합니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        headers = bearer(children[0].user_id)
        body = {"verification_id": str(request.id), "response": "NOT_SENT"}
        assert (
            await client.post(f"{PREFIX}/respond", headers=headers, json=body)
        ).status_code == 200
        deadline = request.expires_at

        class Clock(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                """응답한 자녀의 재전송을 만료 정각으로 고정합니다."""
                return deadline

        monkeypatch.setattr("app.services.verification.datetime", Clock)
        rejected = await client.post(f"{PREFIX}/respond", headers=headers, json=body)
        assert rejected.status_code == 410
        viewed = await client.get(f"{PREFIX}/{request.id}", headers=headers)
        assert viewed.json()["status"] == "EXPIRED"
        assert viewed.json()["responded_count"] == 1
        assert viewed.json()["my_response"] == "NOT_SENT"

    runner.run(scenario())


def test_concurrent_expiry_and_response(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """여러 조회와 응답이 동시에 만료를 감지해도 만료 알림은 한 번만 저장합니다."""

    async def scenario() -> None:
        event, parent, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        deadline = request.expires_at

        class Clock(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                """경합하는 모든 호출을 만료 정각으로 고정합니다."""
                return deadline

        monkeypatch.setattr("app.services.verification.datetime", Clock)
        results = await asyncio.gather(
            client.get(f"{PREFIX}/{request.id}", headers=bearer(parent.user_id)),
            client.get(f"{PREFIX}/{request.id}", headers=bearer(children[1].user_id)),
            client.post(
                f"{PREFIX}/respond",
                headers=bearer(children[0].user_id),
                json={"verification_id": str(request.id), "response": "SENT"},
            ),
        )
        assert [result.status_code for result in results] == [200, 200, 410]
        assert all(result.json()["status"] == "EXPIRED" for result in results[:2])
        rows = await notifications(session, request.id)
        assert (
            sum(row.message_type == NotificationType.VERIFY_EXPIRED for row in rows)
            == 1
        )
        assert await VerificationRepository(session).counts(request.id) == (2, 0, 0)

    runner.run(scenario())


def test_response_waiting_on_lock_rechecks_deadline(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기한 전에 도착했어도 행 잠금 대기 중 기한을 넘기면 응답을 기록하지 않습니다."""

    async def scenario() -> None:
        event, _, children = await prepare_event(session)
        request = await VerificationService(session).create(event.id)
        now = request.expires_at - timedelta(microseconds=1)
        attempted = asyncio.Event()
        original = VerificationRepository.get

        class Clock(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:
                """잠금 해제 전후의 제어된 시각을 반환합니다."""
                return now

        async def observed_get(
            repository: VerificationRepository,
            request_id: UUID,
            *,
            for_update: bool = False,
        ) -> VerificationRequest | None:
            """서비스가 행 잠금을 시도하는 시점을 관찰합니다."""
            attempted.set()
            return await original(repository, request_id, for_update=for_update)

        await VerificationRepository(session).get(request.id, for_update=True)
        monkeypatch.setattr("app.services.verification.datetime", Clock)
        monkeypatch.setattr(VerificationRepository, "get", observed_get)
        task = asyncio.create_task(
            client.post(
                f"{PREFIX}/respond",
                headers=bearer(children[0].user_id),
                json={"verification_id": str(request.id), "response": "SENT"},
            )
        )
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            now = request.expires_at
            await session.commit()
            response = await asyncio.wait_for(task, timeout=5)
            assert response.status_code == 410
            assert await VerificationRepository(session).counts(request.id) == (2, 0, 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    runner.run(scenario())
