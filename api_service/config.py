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

    # UI session (LDAP login)
    session_secret: str = os.getenv("SESSION_SECRET", "change_me_in_prod")

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

    # Optional: local superuser for UI (full access, bypass LDAP). Leave empty to disable.
    ui_superuser_login: str = os.getenv("UI_SUPERUSER_LOGIN", "") or os.getenv("SUPERUSER_LOGIN", "")
    ui_superuser_password: str = os.getenv("UI_SUPERUSER_PASSWORD", "") or os.getenv("SUPERUSER_PASSWORD", "")


settings = Settings()