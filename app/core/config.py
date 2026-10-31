from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import AnyUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """DB·인증·초대 링크의 실행 설정을 읽습니다."""

    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "giantkiller"
    postgres_user: str = "giantkiller"
    postgres_password: str
    auth_secret: SecretStr = Field(min_length=32)
    auth_token_lifetime_seconds: int = Field(default=3600, gt=0)
    invitation_base_url: AnyUrl | None = None
    ai_api_key: SecretStr | None = None
    ai_base_url: AnyUrl = AnyUrl("https://copa.codyssey.kr")
    ai_model: str = "gpt-5.4"
    ai_timeout_seconds: float = Field(default=30, gt=0)
    stt_model: str = "small"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """검증한 설정을 프로세스에서 재사용합니다."""
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
