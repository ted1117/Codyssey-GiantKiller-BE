from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from sqlalchemy.exc import IntegrityError

from app.core.config import SettingsDep
from app.core.database import SessionDep
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate


class UserManager(UUIDIDMixin, BaseUserManager[User, UUID]):
    """FastAPI Users 계정 생성과 이메일 중복 오류를 처리합니다."""

    def __init__(self, repository: UserRepository) -> None:
        """계정 어댑터와 기본 비밀번호 해시 구현을 연결합니다."""
        super().__init__(repository)
        self.repository = repository

    async def create(
        self,
        user_create: UserCreate,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        """이메일을 소문자로 저장하고 동시 중복 가입을 가입 오류로 변환합니다."""
        user_create.email = user_create.email.lower()
        try:
            return await super().create(user_create, safe=safe, request=request)
        except IntegrityError as error:
            await self.repository.session.rollback()
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise exceptions.UserAlreadyExists from error
            raise


async def get_user_repository(session: SessionDep) -> UserRepository:
    """인증에서 사용할 계정 어댑터를 제공합니다."""
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


async def get_user_manager(repository: UserRepositoryDep) -> UserManager:
    """가입·로그인에 사용할 사용자 관리자를 제공합니다."""
    return UserManager(repository)


def get_jwt_strategy(settings: SettingsDep) -> JWTStrategy[User, UUID]:
    """설정된 서명 비밀과 유효 시간으로 JWT 전략을 구성합니다."""
    return JWTStrategy(
        secret=settings.auth_secret.get_secret_value(),
        lifetime_seconds=settings.auth_token_lifetime_seconds,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=BearerTransport(tokenUrl="/api/v1/auth/login"),
    get_strategy=get_jwt_strategy,
)
fastapi_users = FastAPIUsers[User, UUID](get_user_manager, [auth_backend])
current_active_user = fastapi_users.current_user(active=True)
CurrentUser = Annotated[User, Depends(current_active_user)]
