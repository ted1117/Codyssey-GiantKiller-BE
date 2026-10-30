class ServiceError(Exception):
    """서비스에서 예상한 오류를 HTTP 상태와 오류 코드로 전달합니다."""

    def __init__(self, status_code: int, detail: str) -> None:
        """응답 상태와 공개 가능한 오류 코드를 저장합니다."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
