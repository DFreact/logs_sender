import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", extra="ignore")

    event_query_timeout_ms: int = Field(default=3000, ge=100, le=30000)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: int = Field(default=5, ge=1, le=30)
    ingestion_threads: int = Field(default=2, ge=1, le=32)
    delivery_threads: int = Field(default=2, ge=1, le=32)
    maintenance_threads: int = Field(default=2, ge=1, le=8)

    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "eventhub"
    db_user: str = "eventhub_app"
    db_password: SecretStr | None = None
    db_password_file: Path | None = None
    auth_secret: SecretStr | None = None
    auth_secret_file: Path | None = None
    channel_secret: SecretStr | None = None
    channel_secret_file: Path | None = None
    outbound_ca_file: Path | None = None
    outbound_hosts: list[str] = []
    outbound_networks: list[str] = []
    outbound_ports: list[int] = [443, 465, 587]
    telegram_allowed: bool = False
    max_allowed: bool = False
    delivery_timeout_seconds: int = Field(default=15, ge=5, le=30)
    delivery_lease_seconds: int = Field(default=60, ge=30, le=300)
    delivery_retry_delays: list[int] = [30, 120, 600, 1800]
    delivery_jitter_percent: int = Field(default=10, ge=0, le=20)
    allowed_origins: list[str] = ["https://localhost"]
    allow_insecure_local_http: bool = False
    trusted_proxy_networks: list[str] = []
    session_seconds: int = Field(default=28800, ge=60, le=86400)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)
    login_account_limit: int = Field(default=10, ge=1, le=1000)
    login_ip_limit: int = Field(default=30, ge=1, le=5000)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password_file: Path | None = None
    storage_root: Path = Path("/var/lib/eventhub/blobs")
    blob_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    storage_min_free_bytes: int = Field(default=100 * 1024 * 1024, ge=0)
    lease_seconds: int = Field(default=60, ge=10, le=3600)
    publication_seconds: int = Field(default=30, ge=2, le=300)
    background_poll_seconds: float = Field(default=2, ge=0.2, le=10)
    heartbeat_stale_seconds: int = Field(default=30, ge=10, le=300)
    orphan_grace_seconds: int = Field(default=86400, ge=60)
    maintenance_seconds: int = Field(default=60, ge=5, le=3600)
    work_batch_size: int = Field(default=100, ge=1, le=1000)
    ingest_max_bytes: int = Field(default=1024 * 1024, ge=1, le=25 * 1024 * 1024)
    ingest_per_minute: int = Field(default=120, ge=1, le=6000000)
    smtp_public_host: str = Field(default="", max_length=253)
    smtp_public_port: int = Field(default=2525, ge=1, le=65535)
    smtp_host: str = "127.0.0.1"
    smtp_port: int = Field(default=2525, ge=1, le=65535)
    smtp_allowed_networks: list[str] = ["127.0.0.0/8", "::1/128"]
    smtp_recipients: list[str] = ["events@localhost"]
    smtp_max_recipients: int = Field(default=10, ge=1, le=100)
    smtp_max_connections: int = Field(default=20, ge=1, le=100)
    smtp_allow_plaintext: bool = False
    smtp_tls_cert: Path | None = None
    smtp_tls_key: Path | None = None

    @model_validator(mode="after")
    def validate_auth(self):
        for origin in self.allowed_origins:
            url = urlsplit(origin)
            if url.path or url.query or url.fragment or url.username or not url.hostname:
                raise ValueError("INVALID_ORIGIN_CONFIGURATION")
            if self.allow_insecure_local_http:
                local = url.hostname == "localhost"
                try:
                    local = local or ipaddress.ip_address(url.hostname).is_loopback
                except ValueError:
                    pass
                if not local or url.scheme != "http":
                    raise ValueError("LOCAL_HTTP_REQUIRES_LOOPBACK_ORIGIN")
            elif url.scheme != "https":
                raise ValueError("HTTPS_ORIGIN_REQUIRED")
        for network in self.trusted_proxy_networks:
            ipaddress.ip_network(network)
        for network in self.smtp_allowed_networks:
            ipaddress.ip_network(network)
        for network in self.outbound_networks:
            ipaddress.ip_network(network)
        if any(not 1 <= port <= 65535 for port in self.outbound_ports):
            raise ValueError("INVALID_OUTBOUND_PORT")
        if self.delivery_lease_seconds < self.delivery_timeout_seconds + 15:
            raise ValueError("DELIVERY_LEASE_TOO_SHORT")
        if len(self.delivery_retry_delays) > 8 or any(
            type(v) is not int or not 1 <= v <= 86400 for v in self.delivery_retry_delays
        ):
            raise ValueError("INVALID_DELIVERY_RETRY_POLICY")
        return self

    def signing_key(self) -> bytes:
        if self.auth_secret_file:
            value = self.auth_secret_file.read_text().strip()
        elif self.auth_secret:
            value = self.auth_secret.get_secret_value()
        else:
            raise RuntimeError("AUTH_SECRET_REQUIRED")
        if len(value) < 32:
            raise RuntimeError("AUTH_SECRET_TOO_SHORT")
        return value.encode()

    @property
    def secure_cookie(self) -> bool:
        return not self.allow_insecure_local_http

    @property
    def session_cookie(self) -> str:
        return "__Host-eventhub_session" if self.secure_cookie else "eventhub_local_session"

    @property
    def preauth_cookie(self) -> str:
        return "__Host-eventhub_preauth" if self.secure_cookie else "eventhub_local_preauth"

    def database_url(self) -> URL:
        # URL.create preserves special characters without ConfigParser interpolation.
        if self.db_password_file is not None:
            password = self.db_password_file.read_text().strip()
        elif self.db_password is not None:
            password = self.db_password.get_secret_value()
        else:
            raise RuntimeError("DATABASE_SECRET_REQUIRED")
        if not password:
            raise RuntimeError("DATABASE_SECRET_REQUIRED")
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )
