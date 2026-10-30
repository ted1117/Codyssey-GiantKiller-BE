import asyncio
from threading import Lock

import firebase_admin
from firebase_admin import messaging

_initialization_lock = Lock()


def _get_app() -> firebase_admin.App:
    with _initialization_lock:
        try:
            return firebase_admin.get_app("giantkiller-push")
        except ValueError:
            return firebase_admin.initialize_app(
                options={"httpTimeout": 10}, name="giantkiller-push"
            )


class FCMClient:
    """필요할 때 ADC로 초기화해 notification과 data를 비동기로 보냅니다."""

    async def send(
        self, fids: list[str], title: str, body: str, data: dict[str, str]
    ) -> messaging.BatchResponse:
        """최대 500개 FID의 발송 결과를 입력 순서대로 반환합니다.

        Args:
            fids: 대상 FCM FID 목록입니다.
            title: 사용자에게 표시할 제목입니다.
            body: 사용자에게 표시할 안내 문구입니다.
            data: 앱 화면 이동과 응답에 사용할 문자열 데이터입니다.

        Returns:
            FID별 FCM 접수 성공·실패 결과입니다.
        """
        app = await asyncio.to_thread(_get_app)
        messages = [
            messaging.Message(
                fid=fid,
                notification=messaging.Notification(title=title, body=body),
                data=data,
            )
            for fid in fids
        ]
        return await messaging.send_each_async(messages, app=app)
