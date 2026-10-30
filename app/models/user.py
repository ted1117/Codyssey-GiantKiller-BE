from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    """FastAPI Users 계정과 표시 이름을 저장합니다."""

    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(100))
