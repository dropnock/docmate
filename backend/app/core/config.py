from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://docmate:docmate@localhost:5432/docmate"
    secret_key: str = "dev-secret-key-replace-in-production"

    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"
    aws_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    s3_force_path_style: bool = True

    keycloak_internal_url: str = "http://localhost:8180"
    keycloak_external_url: str = "http://localhost:8180"
    keycloak_admin_user: str = "admin"
    keycloak_admin_password: str = "admin"


settings = Settings()
