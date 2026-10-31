import asyncio
import json
from datetime import UTC, datetime
from io import BytesIO
from typing import BinaryIO
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, UploadFile
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import SYSTEM_PROMPT, AIClient, AIRequestError
from app.core.exceptions import ServiceError
from app.models import SecurityEvent
from app.models.family import FamilyRole
from app.repositories.family import FamilyMemberRepository
from app.schemas.event import CallAnalysisResult
from tests.test_api import account, family


def reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def verdict(label: object, confidence: object = 0.9, reason: object = "SECRET") -> str:
    return json.dumps({"label": label, "confidence": confidence, "reason": reason})


def make(
    content: str | None = None,
    *,
    handler=None,
    transcribe=None,
    base_url: str = "https://gw.test",
) -> tuple[AIClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def default(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply(content or ""))

    client = AIClient(
        "key-1",
        base_url=base_url,
        model="m-1",
        transport=httpx.MockTransport(handler or default),
        transcribe=transcribe,
    )
    return client, seen


def test_alert_fraud_mapping() -> None:
    """label 1은 신뢰도에 따라 HIGH·MEDIUM 사기로 변환합니다."""
    high, _ = make(verdict(1, 0.9, "송금 유도"))
    result = asyncio.run(high.analyze_alert("text"))
    assert (result.classification, result.requires_attention) == ("FRAUD", True)
    assert (result.risk_level, result.summary) == ("HIGH", "송금 유도")
    medium, _ = make(verdict(1, 0.5))
    assert asyncio.run(medium.analyze_alert("text")).risk_level == "MEDIUM"


def test_alert_normal_mapping() -> None:
    """label 0은 주의 불필요 정상으로 변환합니다."""
    client, _ = make(verdict(0, 0.99))
    result = asyncio.run(client.analyze_alert("text"))
    assert (result.classification, result.requires_attention) == ("NORMAL", False)
    assert result.risk_level == "LOW"


def test_request_shape() -> None:
    """게이트웨이 경로·인증·모델·프롬프트를 확인합니다."""
    client, seen = make(verdict(0), base_url="https://gw.test/")
    asyncio.run(client.analyze_alert("안녕 내용"))
    request = seen[0]
    assert request.url.path == "/v1/chat/completions"
    assert str(request.url).startswith("https://gw.test/v1")
    assert request.headers["Authorization"] == "Bearer key-1"
    body = json.loads(request.content)
    assert body["model"] == "m-1" and body["temperature"] == 0
    assert body["max_tokens"] == 300
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "안녕 내용" in body["messages"][1]["content"]


@pytest.mark.parametrize(
    "wrapped",
    [
        "판정입니다.\n" + verdict(1) + "\n끝",
        "```json\n" + verdict(1) + "\n```",
    ],
)
def test_wrapped_reply_parses(wrapped: str) -> None:
    """설명이나 코드 펜스에 싸인 JSON도 추출합니다."""
    client, _ = make(wrapped)
    assert asyncio.run(client.analyze_alert("t")).classification == "FRAUD"


@pytest.mark.parametrize(
    "content",
    [
        "죄송합니다",
        verdict(2),
        verdict("1"),
        verdict(True),
        verdict(1, 1.5),
        verdict(1, "0.9"),
        verdict(1, 0.9, "  "),
        json.dumps({"label": 1, "confidence": 0.9}),
        "{not json}",
    ],
)
def test_invalid_reply_rejected(content: str) -> None:
    """해석할 수 없는 응답은 검증 오류로 거부합니다."""
    client, _ = make(content)
    with pytest.raises(ValidationError) as error:
        asyncio.run(client.analyze_alert("t"))
    assert "SECRET" not in str(error.value)


def test_http_errors() -> None:
    """HTTP 오류와 시간 초과를 서비스가 처리하는 예외로 변환합니다."""
    failing, _ = make(handler=lambda request: httpx.Response(500, text="secret"))
    with pytest.raises(AIRequestError) as error:
        asyncio.run(failing.analyze_alert("t"))
    assert isinstance(error.value, OSError)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    slow, _ = make(handler=timeout)
    with pytest.raises(TimeoutError):
        asyncio.run(slow.analyze_alert("t"))
    bad_shape, _ = make(handler=lambda r: httpx.Response(200, json={"choices": []}))
    with pytest.raises(AIRequestError):
        asyncio.run(bad_shape.analyze_alert("t"))


def test_analyze_call() -> None:
    """전사본을 분류해 지시 없는 통화 결과로 반환합니다."""
    received: list[bytes] = []

    def transcribe(file: BinaryIO) -> str:
        received.append(file.read())
        return "계좌로 송금하세요"

    client, seen = make(verdict(1, 0.95), transcribe=transcribe)
    result = asyncio.run(client.analyze_call(UploadFile(BytesIO(b"audio"))))
    assert isinstance(result, CallAnalysisResult)
    assert result.instructions == [] and result.classification == "FRAUD"
    assert received == [b"audio"]
    assert "계좌로 송금하세요" in json.loads(seen[0].content)["messages"][1]["content"]


def test_call_failures() -> None:
    """빈 전사본·전사 오류는 분류 없이 요청 실패입니다."""
    empty, seen = make(verdict(0), transcribe=lambda file: "  ")
    with pytest.raises(AIRequestError):
        asyncio.run(empty.analyze_call(UploadFile(BytesIO(b"a"))))
    assert seen == []

    def broken(file: BinaryIO) -> str:
        raise RuntimeError("private")

    failing, seen = make(verdict(0), transcribe=broken)
    with pytest.raises(AIRequestError) as error:
        asyncio.run(failing.analyze_call(UploadFile(BytesIO(b"a"))))
    assert seen == [] and error.value.__cause__ is None


def test_unconfigured() -> None:
    """키가 없으면 두 분석 모두 503입니다."""
    client = AIClient()
    assert client.configured is False
    for call in (
        client.analyze_alert("t"),
        client.analyze_call(UploadFile(BytesIO(b"a"))),
    ):
        with pytest.raises(ServiceError, match="AI_NOT_CONFIGURED"):
            asyncio.run(call)


def test_alert_api_with_real_client(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """실제 AIClient(가짜 전송)로 문자 API가 위험 사건을 저장합니다."""

    async def scenario() -> None:
        from app.api.dependencies import get_ai_client

        _, child_headers = await account(client)
        family_id = await family(client, child_headers)
        parent_id, headers = await account(client)
        await FamilyMemberRepository(session).create(
            UUID(family_id), UUID(parent_id), FamilyRole.PARENT
        )
        await session.commit()
        ai, _ = make(verdict(1, 0.9, "링크 유도"))
        application.dependency_overrides[get_ai_client] = lambda: ai
        response = await client.post(
            "/api/v1/alerts/check",
            headers=headers,
            json={
                "family_id": family_id,
                "occurred_at": datetime.now(UTC).isoformat(),
                "text": "masked",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["event_id"] and data["result"]["classification"] == "FRAUD"

    runner.run(scenario())


def test_audio_too_large(
    client: AsyncClient,
    application: FastAPI,
    session: AsyncSession,
    runner: asyncio.Runner,
) -> None:
    """10MiB 초과 녹음은 사건 생성 전에 413으로 거부합니다."""

    async def scenario() -> None:
        from app.api.dependencies import get_ai_client

        _, child_headers = await account(client)
        family_id = await family(client, child_headers)
        parent_id, headers = await account(client)
        await FamilyMemberRepository(session).create(
            UUID(family_id), UUID(parent_id), FamilyRole.PARENT
        )
        await session.commit()
        ai, seen = make(verdict(0), transcribe=lambda file: "text")
        application.dependency_overrides[get_ai_client] = lambda: ai
        response = await client.post(
            "/api/v1/calls/analyze",
            headers=headers,
            data={"family_id": family_id, "occurred_at": datetime.now(UTC).isoformat()},
            files={"file": ("a.wav", b"0" * (10 * 1024 * 1024 + 1), "audio/wav")},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "AUDIO_TOO_LARGE"}
        assert seen == []
        count = await session.scalar(select(func.count()).select_from(SecurityEvent))
        assert count == 0

    runner.run(scenario())
