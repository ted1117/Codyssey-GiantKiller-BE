from uuid import UUID

from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository(SQLAlchemyUserDatabase[User, UUID]):
    """FastAPI Users의 계정 저장·조회 구현을 재사용합니다.

    계정 변경은 어댑터가 직접 commit하므로 가족 트랜잭션과 분리해서 사용합니다.
    """

    def __init__(self, session: AsyncSession) -> None:
        """계정 모델과 세션을 어댑터에 연결합니다."""
        super().__init__(session, User)
