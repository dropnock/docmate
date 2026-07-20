from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Baked in at Docker build time (see RELEASING.md) — "dev"/"unknown" locally.
    app_version: str = "dev"
    git_commit: str = "unknown"

    # Full connection string override. Leave unset and use the discrete
    # postgres_* fields below instead — they're built into a URL via
    # SQLAlchemy's URL.create(), which percent-encodes user/password safely
    # no matter what characters they contain (a hand-built f-string doesn't).
    database_url: str | None = None

    postgres_user: str = "docmate"
    postgres_password: str = "docmate"
    postgres_db: str = "docmate"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    secret_key: str = "dev-secret-key-replace-in-production"

    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"
    aws_endpoint_url: str | None = None
    aws_public_endpoint_url: str | None = None  # public-facing URL for presigned URLs
    aws_region: str = "us-east-1"
    s3_force_path_style: bool = True

    keycloak_internal_url: str = "http://localhost:8180"
    keycloak_external_url: str = "http://localhost:8180"
    keycloak_admin_user: str = "admin"
    keycloak_admin_password: str = "admin"

    # Base URL of the customer portal WITHOUT subdomain, e.g. http://docmate.com:8080
    # Used to build per-realm Keycloak redirect URIs: http://<realm>.docmate.com:8080/*
    customer_portal_base_url: str = "http://localhost:8080"

    # Comma-separated public base URLs for the digitizing portal, e.g.
    # "https://www.docmate.example.com,https://digitizing.docmate.example.com".
    # Used to keep the docmate-de Keycloak client's redirect URIs in sync —
    # see keycloak_service.sync_de_client().
    de_portal_base_urls: str = "http://localhost:5173,http://localhost:80"

    # Temporary password assigned to demo/seed users (seed.py, seed_agents.py).
    # Users must change it on first login (requiredActions=UPDATE_PASSWORD).
    seed_default_password: str = "changeme123"

    log_level: str = "INFO"
    environment: str = "development"

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


settings = Settings()
