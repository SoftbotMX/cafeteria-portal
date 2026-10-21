from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTAL_", env_file=".env", extra="ignore")

    database_url: str
    fernet_key: str
    cookie_secure: bool = True
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_usuario: str | None = None
    smtp_contrasena: str | None = None
    smtp_remitente: str | None = None

    @field_validator("smtp_host", "smtp_usuario", "smtp_contrasena", "smtp_remitente", mode="before")
    @classmethod
    def _vacio_es_none(cls, v):
        return None if isinstance(v, str) and v.strip() == "" else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
