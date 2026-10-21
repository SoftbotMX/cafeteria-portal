from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
