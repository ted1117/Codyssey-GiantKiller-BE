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
    """ADC 인증과 동기 발송 전체를 worker thread에서 수행합니다."""

    async def send(
        self,
        fids: list[str],
        title: str,
        body: str,
        data: dict[str, str],
        *,
        data_only: bool = False,
    ) -> messaging.BatchResponse:
        """최대 500개 FID의 발송 결과를 입력 순서대로 반환합니다.

        Args:
            fids: 대상 FCM FID 목록입니다.
            title: 사용자에게 표시할 제목입니다.
            body: 사용자에게 표시할 안내 문구입니다.
            data: 앱 화면 이동과 응답에 사용할 문자열 데이터입니다.
            data_only: True면 notification 없이 높은 우선순위 data 메시지로 보냅니다.

        Returns:
            FID별 FCM 접수 성공·실패 결과입니다.
        """
        return await asyncio.to_thread(
            self._send, fids, title, body, data, data_only=data_only
        )

    def _send(
        self,
        fids: list[str],
        title: str,
        body: str,
        data: dict[str, str],
        *,
        data_only: bool = False,
    ) -> messaging.BatchResponse:
        # SDK의 최초 자격 증명 조회와 토큰 갱신도 동기 I/O를 사용할 수 있다.
        app = _get_app()
        if data_only:
            # 백그라운드에서도 onMessageReceived가 돌도록 data만 높은 우선순위로 보낸다.
            messages = [
                messaging.Message(
                    fid=fid,
                    data=data,
                    android=messaging.AndroidConfig(priority="high"),
                )
                for fid in fids
            ]
        else:
            messages = [
                messaging.Message(
                    fid=fid,
                    notification=messaging.Notification(title=title, body=body),
                    data=data,
                )
                for fid in fids
            ]
        return messaging.send_each(messages, app=app)
