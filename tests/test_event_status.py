import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.family import FamilyRole
from app.repositories.family import FamilyMemberRepository
from tests.test_api import account
from tests.test_event_queries import FIELDS, PREFIX, event, seed_family


@pytest.mark.parametrize(
    ("initial", "target", "code"),
    [
        ("OPEN", "ACKNOWLEDGED", 200),
        ("OPEN", "RESOLVED", 200),
        ("ACKNOWLEDGED", "ACKNOWLEDGED", 200),
        ("ACKNOWLEDGED", "RESOLVED", 200),
        ("RESOLVED", "RESOLVED", 200),
        ("RESOLVED", "ACKNOWLEDGED", 409),
    ],
)
def test_transitions(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
    initial: str,
    target: str,
    code: int,
) -> None:
    """전진·직접 해결·재요청은 허용하고 완료 상태의 역행은 거부합니다."""

    async def scenario() -> None:
        fid, _, _, parent = await seed_family(client, session)
        child_id, child = await account(client)
        await FamilyMemberRepository(session).create(
            UUID(fid), UUID(child_id), FamilyRole.CHILD
        )
        row = event(fid, parent, status=initial)
        session.add(row)
        await session.commit()
        response = await client.patch(
            f"{PREFIX}/{row.id}/status", headers=child, json={"status": target}
        )
        assert response.status_code == code, response.text
        await session.refresh(row)
        assert row.status == (target if code == 200 else initial)
        assert row.analysis_status == "COMPLETED" and row.requires_attention is True
        if code == 200:
            assert set(response.json()) == FIELDS
            detail = (await client.get(f"{PREFIX}/{row.id}", headers=child)).json()
            assert detail.pop("instructions") == []
            assert response.json() == detail
        else:
            assert response.json() == {"detail": "EVENT_STATUS_CONFLICT"}

    runner.run(scenario())


def test_permissions_hidden_and_validation(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """권한·공개 조건·입력 오류 요청은 DB 상태를 변경하지 않습니다."""

    async def scenario() -> None:
        fid, child, parent_headers, parent = await seed_family(client, session)
        _, outsider = await account(client)
        visible = event(fid, parent)
        hidden = [
            event(fid, parent, requires_attention=False, status="NOT_REQUIRED"),
            *[
                event(fid, parent, analysis_status=status, requires_attention=None)
                for status in ["PENDING", "PROCESSING", "FAILED"]
            ],
        ]
        session.add_all([visible, *hidden])
        await session.commit()
        path = f"{PREFIX}/{visible.id}/status"
        body = {"status": "RESOLVED"}
        assert (await client.patch(path, json=body)).status_code == 401
        for headers, detail in [
            (parent_headers, "CHILD_REQUIRED"),
            (outsider, "FAMILY_ACCESS_DENIED"),
        ]:
            response = await client.patch(path, headers=headers, json=body)
            assert response.status_code == 403
            assert response.json() == {"detail": detail}
        for row in hidden:
            response = await client.patch(
                f"{PREFIX}/{row.id}/status", headers=child, json=body
            )
            assert response.status_code == 404
        assert (
            await client.patch(f"{PREFIX}/{uuid4()}/status", headers=child, json=body)
        ).status_code == 404
        for invalid in [
            {},
            {"status": "OPEN"},
            {"status": "NOT_REQUIRED"},
            {"status": "resolved"},
            {"status": None},
            {"status": "RESOLVED", "extra": True},
        ]:
            assert (
                await client.patch(path, headers=child, json=invalid)
            ).status_code == 422
        await session.refresh(visible)
        assert visible.status == "OPEN"
        for row in hidden:
            old_status = row.status
            await session.refresh(row)
            assert row.status == old_status

    runner.run(scenario())


def test_impersonation_and_concurrent_updates(
    client: AsyncClient,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """사칭 문자를 변경하며 동시 확인·해결 요청으로 완료가 역행하지 않습니다."""

    async def scenario() -> None:
        fid, child, _, parent = await seed_family(client, session)
        row = event(
            fid,
            parent,
            event_type="ALERT",
            classification="CHILD_IMPERSONATION",
            analysis_status=None,
            requires_attention=None,
        )
        session.add(row)
        await session.commit()
        responses = await asyncio.gather(
            *[
                client.patch(
                    f"{PREFIX}/{row.id}/status", headers=child, json={"status": status}
                )
                for status in ["ACKNOWLEDGED", "RESOLVED"]
            ]
        )
        assert responses[0].status_code in {200, 409}
        assert responses[1].status_code == 200
        await session.refresh(row)
        assert row.status == "RESOLVED"
        assert row.analysis_status is None and row.requires_attention is None

    runner.run(scenario())
