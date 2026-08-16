from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    secret_key: str
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    algorithm: str = "HS256"

    database_url: str
    redis_url: str

    first_admin_email: str = ""
    first_admin_password: str = ""

    resend_api_key: str = ""
    email_from: str = "Excel Insider <no-reply@excelinsider.com>"
    frontend_url: str = "http://localhost:3000"

    allowed_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]


settings = Settings()