import asyncio
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI, UploadFile
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.ai import AIClient
from app.models import SecurityEvent
from app.models.family import FamilyRole
from app.models.notification import NotificationRequest
from app.repositories.family import FamilyMemberRepository
from app.schemas.analysis import AlertAnalysisResult
from app.schemas.event import CallAnalysisResult
from app.services.analysis import AnalysisService
from tests.test_api import account, family


@pytest.mark.parametrize("kind", ["normal", "risk", "request_error", "invalid"])
def test_call_flow(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    engine: AsyncEngine,
    runner: asyncio.Runner,
    kind: str,
) -> None:
    """HTTP 요청에서 선저장·파일 복구·분석 실패·알림 분기를 검증합니다."""

    async def scenario() -> None:
        from app.api.dependencies import get_analysis_service
        from app.core.database import SessionDep

        _, child_headers = await account(client)
        family_id = await family(client, child_headers)
        parent_id, headers = await account(client)
        await FamilyMemberRepository(session).create(
            UUID(family_id), UUID(parent_id), FamilyRole.PARENT
        )
        await session.commit()
        ai = AsyncMock(spec=AIClient)
        ai.configured = True
        push = AsyncMock()

        async def analyze(file: UploadFile):
            assert await file.read() == b"demo-audio"
            async with AsyncSession(engine) as observer:
                row = await observer.scalar(select(SecurityEvent))
                assert row.analysis_status == "PROCESSING"
            if kind == "request_error":
                raise TimeoutError("private-error")
            if kind == "invalid":
                return {
                    "classification": "CHILD_IMPERSONATION",
                    "requires_attention": False,
                }
            return CallAnalysisResult(
                classification="TEST", requires_attention=kind == "risk"
            )

        ai.analyze_call.side_effect = analyze

        def service(session: SessionDep):
            return AnalysisService(session, ai, push)

        application.dependency_overrides[get_analysis_service] = service
        response = await client.post(
            "/api/v1/calls/analyze",
            headers=headers,
            data={"family_id": family_id, "occurred_at": datetime.now(UTC).isoformat()},
            files={"file": ("demo.wav", b"demo-audio", "audio/wav")},
        )
        failed = kind in {"request_error", "invalid"}
        assert response.status_code == (502 if failed else 200), response.text
        data = response.json()
        assert data["analysis_status"] == ("FAILED" if failed else "COMPLETED")
        row = await session.get(SecurityEvent, UUID(data["event_id"]))
        assert row.analysis_status == data["analysis_status"]
        assert push.send.await_count == (1 if kind == "risk" else 0)
        assert "private-error" not in response.text

    runner.run(scenario())


@pytest.mark.parametrize(
    "kind", ["impersonation", "normal", "invalid", "unconfigured", "child"]
)
def test_alert_flow(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
    kind: str,
) -> None:
    """문자 확인 요청과 기본 503·부모 권한·모순 판정 거부를 검증합니다."""

    async def scenario() -> None:
        from app.api.dependencies import get_analysis_service
        from app.core.database import SessionDep

        _, child_headers = await account(client)
        family_id = await family(client, child_headers)
        parent_id, headers = await account(client)
        await FamilyMemberRepository(session).create(
            UUID(family_id), UUID(parent_id), FamilyRole.PARENT
        )
        await session.commit()
        ai = AsyncMock(spec=AIClient)
        ai.configured = True
        ai.analyze_alert.return_value = {
            "classification": "CHILD_IMPERSONATION" if kind != "normal" else "TEST",
            "requires_attention": kind != "invalid",
        }
        if kind != "unconfigured":

            def service(session: SessionDep):
                return AnalysisService(session, ai, AsyncMock())

            application.dependency_overrides[get_analysis_service] = service
        response = await client.post(
            "/api/v1/alerts/check",
            headers=child_headers if kind == "child" else headers,
            json={
                "family_id": family_id,
                "occurred_at": datetime.now(UTC).isoformat(),
                "text": "masked-message",
            },
        )
        expected = {"invalid": 502, "unconfigured": 503, "child": 403}.get(kind, 200)
        assert response.status_code == expected, response.text
        count = await session.scalar(select(func.count()).select_from(SecurityEvent))
        assert count == (1 if kind == "impersonation" else 0)
        notifications = await session.scalar(
            select(func.count()).select_from(NotificationRequest)
        )
        assert notifications == (2 if kind == "impersonation" else 0)
        if kind == "impersonation":
            assert response.json()["verification_id"]
        if kind == "unconfigured":
            assert response.json() == {"detail": "AI_NOT_CONFIGURED"}
        if kind == "child":
            ai.analyze_alert.assert_not_called()

    runner.run(scenario())


def test_result_invariant() -> None:
    """두 결과 모델이 모순된 사칭 판정을 거부합니다."""
    for model in (AlertAnalysisResult, CallAnalysisResult):
        with pytest.raises(ValidationError):
            model(classification="CHILD_IMPERSONATION", requires_attention=False)


def test_unconfigured_call_closes_file(
    session: AsyncSession, runner: asyncio.Runner
) -> None:
    """미설정으로 거부한 통화도 파일을 정리하고 사건을 만들지 않습니다."""
    from app.core.exceptions import ServiceError
    from tests.test_membership import add_family, add_user

    async def scenario() -> None:
        family_row, _ = await add_family(session)
        parent = await add_user(session)
        await FamilyMemberRepository(session).create(
            family_row.id, parent.id, FamilyRole.PARENT
        )
        await session.commit()
        file = UploadFile(BytesIO(b"audio"))
        with pytest.raises(ServiceError, match="AI_NOT_CONFIGURED"):
            await AnalysisService(session, AIClient(), AsyncMock()).analyze_call(
                parent.id, family_row.id, datetime.now(UTC), file
            )
        assert file.file.closed
        assert (
            await session.scalar(select(func.count()).select_from(SecurityEvent)) == 0
        )

    runner.run(scenario())


def test_validation_hides_text(client: AsyncClient, runner: asyncio.Runner) -> None:
    """요청 검증 오류에 문자 원문이 포함되지 않습니다."""

    async def scenario() -> None:
        _, headers = await account(client)
        response = await client.post(
            "/api/v1/alerts/check",
            headers=headers,
            json={
                "family_id": "bad",
                "occurred_at": "bad",
                "text": "private-text\u0000",
            },
        )
        assert response.status_code == 422
        assert "private-text" not in response.text

    runner.run(scenario())


def test_alert_creation_rollback(
    session: AsyncSession, runner: asyncio.Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """확인 생성 실패는 먼저 flush한 사칭 사건도 롤백합니다."""
    from app.schemas.analysis import AlertCheck
    from app.services.verification import VerificationService
    from tests.test_membership import add_family, add_user

    async def scenario() -> None:
        row, _ = await add_family(session)
        parent = await add_user(session)
        await FamilyMemberRepository(session).create(
            row.id, parent.id, FamilyRole.PARENT
        )
        await session.commit()
        ai = AsyncMock(spec=AIClient)
        ai.configured = True
        ai.analyze_alert.return_value = AlertAnalysisResult(
            classification="CHILD_IMPERSONATION", requires_attention=True
        )
        monkeypatch.setattr(
            VerificationService,
            "create",
            AsyncMock(side_effect=RuntimeError("storage")),
        )
        with pytest.raises(RuntimeError, match="storage"):
            await AnalysisService(session, ai, AsyncMock()).check_alert(
                parent.id,
                AlertCheck(
                    family_id=row.id, occurred_at=datetime.now(UTC), text="masked"
                ),
            )
        assert (
            await session.scalar(select(func.count()).select_from(SecurityEvent)) == 0
        )

    runner.run(scenario())
