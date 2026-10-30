from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.verification import VerificationAnswer, VerificationStatus
from app.schemas.verification import VerificationRead, VerificationRespond


@pytest.mark.parametrize("answer", ["SENT", "NOT_SENT"])
def test_valid_answer(answer: str) -> None:
    """발송 여부와 요청 ID만 입력받습니다."""
    request_id = uuid4()
    body = VerificationRespond(verification_id=str(request_id), response=answer)
    assert body.verification_id == request_id
    assert body.response == VerificationAnswer(answer)


@pytest.mark.parametrize(
    "change",
    [
        {"verification_id": "invalid"},
        {"response": "DENIED"},
        {"response": "sent"},
        {"response": None},
        {"user_id": str(uuid4())},
        {"family_id": str(uuid4())},
        {"responded_at": "2026-10-30T00:00:00Z"},
    ],
)
def test_invalid_or_server_owned_input(change: dict[str, object]) -> None:
    """잘못된 응답과 서버 지정 필드 조작을 거부합니다."""
    data = {"verification_id": str(uuid4()), "response": "SENT"} | change
    with pytest.raises(ValidationError):
        VerificationRespond.model_validate(data)


@pytest.mark.parametrize("status", list(VerificationStatus))
def test_shared_result_statuses(status: VerificationStatus) -> None:
    """네 상태를 공통 결과로 직렬화하며 응답 수가 대상 수보다 작아도 됩니다."""
    result = VerificationRead(
        id=uuid4(),
        event_id=uuid4(),
        status=status,
        expires_at=datetime.now(UTC),
        target_count=3,
        responded_count=1,
        my_response=VerificationAnswer.NOT_SENT,
    )
    data = result.model_dump(mode="json")
    assert data["status"] == status.value
    assert data["my_response"] == "NOT_SENT"
    assert set(data) == {
        "id",
        "event_id",
        "status",
        "expires_at",
        "target_count",
        "responded_count",
        "my_response",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"target_count": -1},
        {"responded_count": -1},
        {"expires_at": datetime(2026, 10, 30)},
        {"status": "SAFE"},
    ],
)
def test_invalid_result(change: dict[str, object]) -> None:
    """음수 집계·타임존 없는 시각·안전 판정 상태를 거부합니다."""
    data = {
        "id": uuid4(),
        "event_id": uuid4(),
        "status": "PENDING",
        "expires_at": datetime.now(UTC),
        "target_count": 0,
        "responded_count": 0,
        "my_response": None,
    } | change
    with pytest.raises(ValidationError):
        VerificationRead.model_validate(data)
