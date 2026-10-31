from fastapi import UploadFile

from app.core.exceptions import ServiceError
from app.schemas.analysis import AlertAnalysisResult
from app.schemas.event import CallAnalysisResult


class AIClient:
    """실제 계약 연결 전에는 분석 요청을 거부합니다."""

    configured: bool = False

    async def analyze_alert(self, text: str) -> AlertAnalysisResult:
        """미설정 문자 분석을 거부합니다."""
        raise ServiceError(503, "AI_NOT_CONFIGURED")

    async def analyze_call(self, file: UploadFile) -> CallAnalysisResult:
        """미설정 통화 분석을 거부합니다."""
        raise ServiceError(503, "AI_NOT_CONFIGURED")
