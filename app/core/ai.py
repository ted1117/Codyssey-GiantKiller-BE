import asyncio
import re
import threading
from collections.abc import Callable
from typing import Any, BinaryIO, Literal

import httpx
from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.exceptions import ServiceError
from app.schemas.analysis import AlertAnalysisResult
from app.schemas.event import CallAnalysisResult

SYSTEM_PROMPT = """\
당신은 한국 금융·통신 사기 탐지 분석가입니다. 주어진 내용(문자 메시지 또는 통화 전사본)을
아래 셋 중 하나로 분류합니다.
- CHILD_IMPERSONATION: 자녀·손주 등 가족을 사칭하는 경우. 휴대폰 고장·분실·번호 변경 등을
  핑계로 새 번호로 연락을 유도하거나, 송금·상품권 구매·신분증·계좌·인증번호·앱 설치를 요구하는 경우
- FRAUD: 그 외 스미싱/보이스피싱/스팸. 기관·금융사·택배 사칭, 수상한 링크·앱 설치 유도,
  개인정보·계좌·인증번호 요구, 송금·대출 유도, 가족 이외 지인 사칭, 도박·성인·대출 광고 등
- NORMAL: 지인 간 일상 대화, 정상적인 안내·알림, 내용에 위험 신호가 없는 경우
통화 전사본은 음성인식 오류가 섞여 있을 수 있습니다. 반드시 JSON 한 줄로만 답하세요:
{"category": "CHILD_IMPERSONATION" 또는 "FRAUD" 또는 "NORMAL", "confidence": 0~1, "reason": "한국어 한 문장 근거"}"""  # noqa: E501
HIGH_CONFIDENCE = 0.8
MAX_INPUT_CHARS = 4000

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)
_stt_models: dict[tuple[str, str, str], Any] = {}
_stt_lock = threading.Lock()


class AIRequestError(ConnectionError):
    """AI 게이트웨이 호출이나 음성 인식 실패를 나타냅니다."""


class _Verdict(BaseModel):
    """LLM이 반환한 3분류 판정입니다."""

    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    category: Literal["CHILD_IMPERSONATION", "FRAUD", "NORMAL"]
    confidence: float = Field(ge=0, le=1)
    reason: str

    @field_validator("reason")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("근거는 비어 있을 수 없습니다")
        return value


def _load(name: str, device: str, compute_type: str) -> Any:
    key = (name, device, compute_type)
    with _stt_lock:
        if key not in _stt_models:
            from faster_whisper import WhisperModel

            _stt_models[key] = WhisperModel(
                name, device=device, compute_type=compute_type
            )
        return _stt_models[key]


def load_stt_model(name: str, device: str = "cpu", compute_type: str = "int8") -> None:
    """Whisper 모델을 장치·정밀도별로 프로세스에서 한 번만 미리 불러옵니다."""
    _load(name, device, compute_type)


def _transcribe(
    name: str, file: BinaryIO, device: str = "cpu", compute_type: str = "int8"
) -> str:
    """음성 파일을 한국어 전사본으로 변환합니다."""
    segments, _ = _load(name, device, compute_type).transcribe(file, language="ko")
    return "\n".join(segment.text.strip() for segment in segments)


class AIClient:
    """Codyssey LLM 게이트웨이와 로컬 음성 인식으로 문자·통화를 분석합니다."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://copa.codyssey.kr",
        model: str = "gpt-5.4",
        timeout: float = 30,
        stt_model: str = "small",
        stt_device: str = "cpu",
        stt_compute_type: str = "int8",
        transport: httpx.AsyncBaseTransport | None = None,
        transcribe: Callable[[BinaryIO], str] | None = None,
    ) -> None:
        """게이트웨이 설정을 저장합니다. 키가 없으면 미설정 상태입니다."""
        self.api_key = api_key
        self.configured = api_key is not None
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.transcribe = transcribe or (
            lambda file: _transcribe(stt_model, file, stt_device, stt_compute_type)
        )

    async def _classify(self, text: str) -> _Verdict:
        """게이트웨이에 한 번 요청해 3분류 판정을 검증합니다."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "다음 내용을 판별하세요.\n\n"
                    f"<내용>\n{text[:MAX_INPUT_CHARS]}\n</내용>",
                },
            ],
            "max_tokens": 300,
            "temperature": 0,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError:
            raise AIRequestError from None
        except ValueError, LookupError, TypeError:
            raise AIRequestError from None
        if not isinstance(content, str):
            raise AIRequestError
        found = _JSON_OBJECT.search(content)
        return _Verdict.model_validate_json(found.group() if found else "")

    async def analyze_alert(self, text: str) -> AlertAnalysisResult:
        """문자를 분류해 문자 분석 결과로 변환합니다."""
        if not self.configured:
            raise ServiceError(503, "AI_NOT_CONFIGURED")
        return AlertAnalysisResult.model_validate(_result(await self._classify(text)))

    async def analyze_call(self, file: UploadFile) -> CallAnalysisResult:
        """통화 녹음을 전사한 뒤 분류해 통화 분석 결과로 변환합니다."""
        if not self.configured:
            raise ServiceError(503, "AI_NOT_CONFIGURED")
        try:
            transcript = await asyncio.to_thread(self.transcribe, file.file)
        except Exception:
            raise AIRequestError from None
        if not transcript.strip():
            raise AIRequestError
        verdict = await self._classify(transcript)
        return CallAnalysisResult.model_validate(
            {**_result(verdict), "instructions": []}
        )


def _result(verdict: _Verdict) -> dict[str, Any]:
    """3분류 판정을 내부 분류·위험 수준으로 매핑합니다."""
    if verdict.category == "NORMAL":
        return {
            "classification": "NORMAL",
            "requires_attention": False,
            "risk_level": "LOW",
            "summary": verdict.reason,
        }
    return {
        "classification": verdict.category,
        "requires_attention": True,
        "risk_level": "HIGH" if verdict.confidence >= HIGH_CONFIDENCE else "MEDIUM",
        "summary": verdict.reason,
    }
