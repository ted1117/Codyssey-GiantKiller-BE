import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SecurityEvent
from app.models.family import FamilyRole
from app.repositories.family import FamilyMemberRepository
from tests.test_api import account, family

PREFIX = "/api/v1/events"
FIELDS = {"id", "summary", "analysis_status", "status", "occurred_at"}


async def seed_family(
    client: AsyncClient, session: AsyncSession
) -> tuple[str, dict[str, str], dict[str, str], UUID]:
    """로그인한 자녀와 부모가 속한 가족을 준비합니다."""
    _, child = await account(client)
    family_id = await family(client, child)
    parent_id, parent = await account(client)
    member = await FamilyMemberRepository(session).create(
        UUID(family_id), UUID(parent_id), FamilyRole.PARENT
    )
    member_id = member.id
    await session.commit()
    return family_id, child, parent, member_id


def event(family_id: str, parent_id: UUID, **changes: object) -> SecurityEvent:
    """공개 가능한 통화 사건을 만들고 필요한 필드만 변경합니다."""
    return SecurityEvent(
        **{
            "family_id": UUID(family_id),
            "parent_member_id": parent_id,
            "event_type": "CALL",
            "classification": "TEST",
            "requires_attention": True,
            "analysis_status": "COMPLETED",
            "status": "OPEN",
            "summary": "사건 요약",
            "occurred_at": datetime.now(UTC),
            **changes,
        }
    )


def test_list_detail_and_pagination(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """공개 사건만 정렬·페이지 처리하고 목록과 상세의 최소 필드를 검증합니다."""

    async def scenario() -> None:
        fid, child, _, parent = await seed_family(client, session)
        other, _, _, other_parent = await seed_family(client, session)
        now = datetime.now(UTC)
        oldest = event(fid, parent, occurred_at=now - timedelta(days=1))
        same_time = [
            event(fid, parent, occurred_at=now, status=status)
            for status in ["ACKNOWLEDGED", "RESOLVED"]
        ]
        alert = event(
            fid,
            parent,
            event_type="ALERT",
            classification="CHILD_IMPERSONATION",
            analysis_status=None,
            requires_attention=None,
            occurred_at=now + timedelta(seconds=1),
            summary=None,
        )
        hidden = event(fid, parent, requires_attention=False, status="NOT_REQUIRED")
        session.add_all([oldest, *same_time, alert, hidden, event(other, other_parent)])
        await session.commit()
        rows = (
            await client.get(PREFIX, headers=child, params={"family_id": fid})
        ).json()
        ordered = sorted(
            same_time, key=lambda row: (row.created_at, row.id), reverse=True
        )
        assert [row["id"] for row in rows] == [
            str(row.id) for row in [alert, *ordered, oldest]
        ]
        assert all(set(row) == FIELDS for row in rows)
        page = await client.get(
            PREFIX, headers=child, params={"family_id": fid, "limit": 2, "offset": 1}
        )
        assert page.json() == rows[1:3]
        for row in rows:
            detail = await client.get(f"{PREFIX}/{row['id']}", headers=child)
            assert detail.status_code == 200
            assert detail.json() == row
        other_child_id, other_child = await account(client)
        await FamilyMemberRepository(session).create(
            UUID(fid), UUID(other_child_id), FamilyRole.CHILD
        )
        await session.commit()
        # 대표가 아닌 자녀도 같은 공개 범위를 조회할 수 있다.
        assert (
            await client.get(PREFIX, headers=other_child, params={"family_id": fid})
        ).json() == rows
        assert (
            await client.get(f"{PREFIX}/{alert.id}", headers=other_child)
        ).status_code == 200
        # 조회만으로 대응 상태를 변경하지 않는다.
        await session.refresh(alert)
        assert alert.status == "OPEN"

    runner.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"requires_attention": False, "status": "NOT_REQUIRED"},
        {"analysis_status": "PENDING", "requires_attention": None},
        {"analysis_status": "PROCESSING", "requires_attention": None},
        {"analysis_status": "FAILED", "requires_attention": None},
        {"analysis_status": None, "requires_attention": None},
        {"event_type": "ALERT", "classification": "SAFE", "requires_attention": None},
    ],
)
def test_hidden_events(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    changes: dict[str, object],
) -> None:
    """정상·미분석·실패 사건은 목록에서 숨기고 ID 직접 조회도 거부합니다."""

    async def scenario() -> None:
        fid, child, _, parent = await seed_family(client, session)
        row = event(fid, parent, **changes)
        session.add(row)
        await session.commit()
        response = await client.get(PREFIX, headers=child, params={"family_id": fid})
        assert response.status_code == 200 and response.json() == []
        detail = await client.get(f"{PREFIX}/{row.id}", headers=child)
        assert detail.status_code == 404
        assert detail.json() == {"detail": "EVENT_NOT_FOUND"}

    runner.run(scenario())


def test_permissions_and_missing_events(
    client: AsyncClient, session: AsyncSession, runner: asyncio.Runner
) -> None:
    """미인증·부모·타 가족 접근과 없는 가족·사건의 오류를 검증합니다."""

    async def scenario() -> None:
        fid, child, parent_headers, parent = await seed_family(client, session)
        _, outsider = await account(client)
        row = event(fid, parent)
        session.add(row)
        await session.commit()
        for path in [f"{PREFIX}?family_id={fid}", f"{PREFIX}/{row.id}"]:
            assert (await client.get(path)).status_code == 401
            for headers, code in [
                (parent_headers, "CHILD_REQUIRED"),
                (outsider, "FAMILY_ACCESS_DENIED"),
            ]:
                response = await client.get(path, headers=headers)
                assert response.status_code == 403
                assert response.json() == {"detail": code}
        assert (
            await client.get(f"{PREFIX}/{uuid4()}", headers=child)
        ).status_code == 404
        missing = await client.get(
            PREFIX, headers=child, params={"family_id": str(uuid4())}
        )
        assert missing.status_code == 404

    runner.run(scenario())


def test_query_validation(client: AsyncClient, runner: asyncio.Runner) -> None:
    """페이지 범위와 UUID 입력을 검증합니다."""

    async def scenario() -> None:
        _, child = await account(client)
        fid = await family(client, child)
        queries = ["", "family_id=bad"] + [
            f"family_id={fid}&{query}"
            for query in ["limit=0", "limit=101", "offset=-1", "limit=bad"]
        ]
        for query in queries:
            assert (
                await client.get(f"{PREFIX}?{query}", headers=child)
            ).status_code == 422
        assert (await client.get(f"{PREFIX}/bad", headers=child)).status_code == 422

    runner.run(scenario())
