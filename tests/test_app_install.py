import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SecurityEvent
from app.models.family import FamilyRole
from app.repositories.family import FamilyMemberRepository
from tests.test_api import account
from tests.test_event_queries import event, seed_family

URL = "/api/v1/actions/app-install"
TEAMVIEWER = "com.teamviewer.quicksupport.market"


def payload(family_id: str, **changes: object) -> dict[str, object]:
    """앱 설치 요청 본문을 만듭니다."""
    return {
        "family_id": family_id,
        "package_name": TEAMVIEWER,
        "app_label": "팀뷰어",
        "is_unknown_source": False,
        "occurred_at": datetime.now(UTC).isoformat(),
        **changes,
    }


async def count_events(session: AsyncSession, event_type: str) -> int:
    """저장된 사건 수를 센다."""
    return await session.scalar(
        select(func.count())
        .select_from(SecurityEvent)
        .where(SecurityEvent.event_type == event_type)
    )


def push_mock(application: FastAPI) -> AsyncMock:
    """conftest가 주입한 공유 발송 mock을 가져옵니다."""
    from app.api.dependencies import get_push_service

    return application.dependency_overrides[get_push_service]()


async def add_child(client: AsyncClient, session: AsyncSession, fid: str) -> UUID:
    """가족에 자녀를 한 명 더 추가합니다."""
    user_id, _ = await account(client)
    await FamilyMemberRepository(session).create(
        UUID(fid), UUID(user_id), FamilyRole.CHILD
    )
    await session.commit()
    return UUID(user_id)


def test_remote_app_with_recent_call(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """원격제어 앱과 최근 의심 통화는 사건·related_call·자녀 푸시를 만듭니다."""

    async def scenario() -> None:
        fid, child, parent, member = await seed_family(client, session)
        second = await add_child(client, session, fid)
        session.add(
            event(fid, member, occurred_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await session.commit()
        push = push_mock(application)
        push.send.reset_mock()
        response = await client.post(URL, headers=parent, json=payload(fid))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["matched"] is True and body["related_call"] is True
        row = await session.scalar(
            select(SecurityEvent).where(SecurityEvent.event_type == "APP_INSTALL")
        )
        assert str(row.id) == body["event_id"]
        assert row.classification == "RISKY_APP_INSTALL"
        assert row.risk_level == "HIGH" and row.requires_attention is True
        assert row.analysis_status == "COMPLETED" and row.status == "OPEN"
        assert row.parent_member_id == member
        assert row.summary == "최근 의심 통화 뒤 '팀뷰어' 앱 설치가 감지됐어요."
        assert TEAMVIEWER not in row.summary
        sent = [c.args[0] for c in push.send.await_args_list]
        assert {m.message_type.value for m in sent} == {"INSTALL_WARNING"}
        assert len(sent) == 2 and {m.event_id for m in sent} == {row.id}
        assert second in {m.user_id for m in sent}
        parent_user = (await client.get("/api/v1/users/me", headers=parent)).json()[
            "id"
        ]
        assert str(parent_user) not in {str(m.user_id) for m in sent}
        # 자녀 목록·상세에 공개된다.
        listing = await client.get(
            "/api/v1/events", headers=child, params={"family_id": fid}
        )
        assert [
            i["id"] for i in listing.json() if i["event_type"] == "APP_INSTALL"
        ] == [body["event_id"]]
        detail = await client.get(f"/api/v1/events/{body['event_id']}", headers=child)
        assert detail.status_code == 200
        assert detail.json()["event_type"] == "APP_INSTALL"

    runner.run(scenario())


def test_unknown_source_without_call(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """출처 불명 앱은 통화가 없어도 저장되며 요약 문구가 달라집니다."""

    async def scenario() -> None:
        fid, _, parent, _ = await seed_family(client, session)
        push_mock(application).send.reset_mock()
        response = await client.post(
            URL,
            headers=parent,
            json=payload(fid, package_name="com.example.app", is_unknown_source=True),
        )
        body = response.json()
        assert response.status_code == 200
        assert body["matched"] is True and body["related_call"] is False
        row = await session.scalar(
            select(SecurityEvent).where(SecurityEvent.event_type == "APP_INSTALL")
        )
        assert row.summary == "위험할 수 있는 '팀뷰어' 앱 설치가 감지됐어요."

    runner.run(scenario())


def test_normal_app_not_stored(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """일반 앱은 사건과 푸시 없이 matched=false를 반환합니다."""

    async def scenario() -> None:
        fid, _, parent, _ = await seed_family(client, session)
        push = push_mock(application)
        push.send.reset_mock()
        response = await client.post(
            URL, headers=parent, json=payload(fid, package_name="com.kakao.talk")
        )
        assert response.status_code == 200
        assert response.json() == {
            "matched": False,
            "event_id": None,
            "related_call": False,
        }
        assert await count_events(session, "APP_INSTALL") == 0
        push.send.assert_not_awaited()

    runner.run(scenario())


def test_related_call_requires_recent_attention_call_in_family(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """정상·다른 가족·24시간 초과 통화만 있으면 related_call은 false입니다."""

    async def scenario() -> None:
        fid, _, parent, member = await seed_family(client, session)
        other_fid, _, _, other_member = await seed_family(client, session)
        now = datetime.now(UTC)
        session.add_all(
            [
                event(fid, member, requires_attention=False, occurred_at=now),
                event(fid, member, analysis_status="PROCESSING", occurred_at=now),
                event(other_fid, other_member, occurred_at=now),
                event(fid, member, occurred_at=now - timedelta(hours=25)),
            ]
        )
        await session.commit()
        response = await client.post(URL, headers=parent, json=payload(fid))
        assert response.status_code == 200
        assert response.json()["matched"] is True
        assert response.json()["related_call"] is False

    runner.run(scenario())


def test_push_failure_keeps_event_and_hides_error(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """발송 예외는 사건과 200 응답을 유지하고 예외 문구를 기록하지 않습니다."""

    async def scenario() -> None:
        fid, _, parent, _ = await seed_family(client, session)
        push = push_mock(application)
        push.send.reset_mock()
        push.send.side_effect = RuntimeError("secret-failure-text")
        try:
            with caplog.at_level(logging.INFO):
                response = await client.post(URL, headers=parent, json=payload(fid))
        finally:
            push.send.side_effect = None
        assert response.status_code == 200 and response.json()["matched"] is True
        assert await count_events(session, "APP_INSTALL") == 1
        assert "install_push_failed" in caplog.text
        assert "RuntimeError" in caplog.text
        assert "secret-failure-text" not in caplog.text
        assert "팀뷰어" not in caplog.text and TEAMVIEWER not in caplog.text

    runner.run(scenario())


def test_forbidden_and_validation(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """자녀·비구성원은 403이고 잘못된 입력은 422이며 저장하지 않습니다."""

    async def scenario() -> None:
        fid, child, parent, _ = await seed_family(client, session)
        _, outsider = await account(client)
        for headers in (child, outsider):
            response = await client.post(URL, headers=headers, json=payload(fid))
            assert response.status_code == 403
        assert (await client.post(URL, json=payload(fid))).status_code == 401
        bad = [
            payload(fid, occurred_at="2026-10-31T10:00:00"),
            payload(fid, app_label="   "),
            payload(fid, package_name=""),
            payload(fid, is_unknown_source="yes"),
            {**payload(fid), "extra": 1},
        ]
        for body in bad:
            response = await client.post(URL, headers=parent, json=body)
            assert response.status_code == 422
        assert await count_events(session, "APP_INSTALL") == 0
        push_mock(application).send.assert_not_awaited()

    runner.run(scenario())
