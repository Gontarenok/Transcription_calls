import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "5000"))

    api_key_911: str = os.getenv("API_KEY_911", "")
    api_key_kc: str = os.getenv("API_KEY_KC", "")
    api_key_admin: str = os.getenv("API_KEY_ADMIN", "")

    qdrant_url: str = os.getenv("QDRANT_URL", "")
    # Backward-compat: env может называться QDRANT_API или QDRANT_API_KEY
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "") or os.getenv("QDRANT_API", "")
    # Backward-compat: коллекция может называться QDRANT_COLLECTION_NAME
    qdrant_collection_topics: str = os.getenv("QDRANT_COLLECTION_TOPICS", "") or os.getenv("QDRANT_COLLECTION_NAME", "topics_spravochnik")

    # UI session (форма входа LDAP / суперпользователь; в режиме trusted_headers почти не используется)
    session_secret: str = os.getenv("SESSION_SECRET", "change_me_in_prod")

    # UI: trusted_headers (прод: OAuth2/OIDC/AD через reverse-proxy) | ldap (локальная отладка)
    ui_auth_mode: str = (os.getenv("UI_AUTH_MODE", "trusted_headers") or "trusted_headers").strip().lower()

    # Доверенные заголовки (только за reverse-proxy, срез внешних X-Forwarded-* на границе)
    # Дефолты соответствуют OAuth2-Proxy (set_xauthrequest=true). На dev/тесте
    # можно переопределить на X-Forwarded-* или любые другие имена через .env.
    trusted_header_login: str = os.getenv("TRUSTED_HEADER_LOGIN", "X-Auth-Request-Preferred-Username")
    trusted_header_roles: str = os.getenv("TRUSTED_HEADER_ROLES", "X-Auth-Request-Roles")
    trusted_header_groups: str = os.getenv("TRUSTED_HEADER_GROUPS", "X-Auth-Request-Groups")
    trusted_prefer_roles_header: bool = (
        os.getenv("TRUSTED_PREFER_ROLES_HEADER", "1").strip().lower() in {"1", "true", "yes", "on"}
    )

    # Сопоставление заголовка ролей (список через запятую от прокси), регистр игнорируется
    trusted_role_admin: str = os.getenv("TRUSTED_ROLE_ADMIN", "admin")
    trusted_role_kc: str = os.getenv("TRUSTED_ROLE_KC", "kc_cc")
    trusted_role_kc_catalog: str = os.getenv("TRUSTED_ROLE_KC_CATALOG", "kc_catalog")
    trusted_role_911: str = os.getenv("TRUSTED_ROLE_911", "911")

    # Сопоставление групп AD по подстроке в значении заголовка (прокси может отдавать CN или короткое имя)
    trusted_group_admin: str = os.getenv(
        "TRUSTED_GROUP_ADMIN",
        "AG-AI calls-Administrators",
    )
    trusted_group_kc: str = os.getenv("TRUSTED_GROUP_KC", "FG-AI calls CC-Users")
    trusted_group_kc_catalog: str = os.getenv(
        "TRUSTED_GROUP_KC_CATALOG",
        "FG-AI calls CC directory-Users",
    )
    trusted_group_911: str = os.getenv("TRUSTED_GROUP_911", "FG-AI calls 911-Users")

    # LDAP / Active Directory
    ldap_url: str = os.getenv("LDAP_URL", "")
    ldap_starttls: bool = (os.getenv("LDAP_STARTTLS", "1").strip().lower() in {"1", "true", "yes", "on"})
    ldap_base_dn: str = os.getenv("LDAP_BASE_DN", "")
    ldap_bind_dn: str = os.getenv("LDAP_BIND_DN", "")
    ldap_bind_password: str = os.getenv("LDAP_BIND_PASSWORD", "")
    # Example: (&(objectClass=user)(sAMAccountName={username}))
    ldap_user_filter: str = os.getenv("LDAP_USER_FILTER", "(&(objectClass=user)(sAMAccountName={username}))")

    ldap_group_dn_admin: str = os.getenv("LDAP_GROUP_DN_ADMIN", "")
    ldap_group_dn_911: str = os.getenv("LDAP_GROUP_DN_911", "")
    ldap_group_dn_kc: str = os.getenv("LDAP_GROUP_DN_KC", "")
    ldap_group_dn_kc_catalog: str = os.getenv("LDAP_GROUP_DN_KC_CATALOG", "")

    # Суперпользователь UI: при UI_SUPERUSER_ENABLED=1 и заданных логине/пароле доступна форма входа
    # даже при UI_AUTH_MODE=trusted_headers (удобно для dev; в проде оставьте 0).
    ui_superuser_enabled: bool = os.getenv("UI_SUPERUSER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

    # Optional: local superuser for UI (full access, bypass LDAP). Leave empty to disable.
    ui_superuser_login: str = os.getenv("UI_SUPERUSER_LOGIN", "") or os.getenv("SUPERUSER_LOGIN", "")
    ui_superuser_password: str = os.getenv("UI_SUPERUSER_PASSWORD", "") or os.getenv("SUPERUSER_PASSWORD", "")

    # Cookie: за HTTPS выставить secure (прод за reverse-proxy с TLS)
    session_cookie_secure: bool = os.getenv("SESSION_COOKIE_SECURE", "1").strip().lower() in {"1", "true", "yes", "on"}

    # Логи: JSON в stdout (удобно для Loki/ELK); иначе plain text
    log_json: bool = os.getenv("LOG_JSON", "1").strip().lower() in {"1", "true", "yes", "on"}
    log_level: str = (os.getenv("LOG_LEVEL", "INFO") or "INFO").strip().upper()

    # Prometheus /metrics (внутренняя сеть / мониторинг)
    prometheus_enabled: bool = os.getenv("PROMETHEUS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}

    # OpenTelemetry OTLP (Jaeger, Tempo и т.д.) — задайте OTEL_EXPORTER_OTLP_ENDPOINT
    otel_enabled: bool = os.getenv("OTEL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


settings = Settings()