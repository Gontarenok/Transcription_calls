from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException, Query, Request, status
from ldap3 import ALL, Connection, Server, Tls

from api_service.config import settings

ROLE_911 = "911"
ROLE_KC = "КЦ"
ROLE_ADMIN = "ADMIN"


def resolve_role_by_key(key: str | None) -> str | None:
    if not key:
        return None
    if settings.api_key_admin and key == settings.api_key_admin:
        return ROLE_ADMIN
    if settings.api_key_911 and key == settings.api_key_911:
        return ROLE_911
    if settings.api_key_kc and key == settings.api_key_kc:
        return ROLE_KC
    return None


def get_current_role(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> str:
    key = x_api_key or api_key
    role = resolve_role_by_key(key)
    if role:
        return role
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def allowed_call_types_for_role(role: str) -> set[str]:
    if role == ROLE_911:
        return {"911"}
    if role == ROLE_KC:
        return {"КЦ"}
    return {"911", "КЦ"}


@dataclass(frozen=True)
class UiIdentity:
    username: str
    role: str
    groups: list[str]


def _normalize_dn(value: str) -> str:
    return (value or "").strip().lower()


def _extract_memberof(entry: Any) -> list[str]:
    try:
        vals = entry.memberOf.values if hasattr(entry, "memberOf") else []
    except Exception:
        vals = []
    out: list[str] = []
    for v in vals or []:
        s = str(v).strip()
        if s:
            out.append(s)
    return out


def ldap_authenticate_and_resolve_role(*, username: str, password: str) -> UiIdentity:
    """
    AD auth flow:
    - connect (StartTLS optional)
    - bind with service account (to search user DN)
    - search user entry by filter
    - bind as user to validate password
    - read memberOf groups and map to role
    """
    if not settings.ldap_url:
        raise HTTPException(status_code=500, detail="LDAP is not configured (LDAP_URL missing)")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username/password")

    server = Server(settings.ldap_url, get_info=ALL)
    # Service bind for search (recommended for AD)
    conn = Connection(server, user=settings.ldap_bind_dn or None, password=settings.ldap_bind_password or None, auto_bind=True)
    if settings.ldap_starttls:
        try:
            conn.start_tls()
        except Exception as exc:
            conn.unbind()
            raise HTTPException(status_code=500, detail=f"LDAP StartTLS failed: {exc}")

    base_dn = settings.ldap_base_dn or ""
    if not base_dn:
        conn.unbind()
        raise HTTPException(status_code=500, detail="LDAP_BASE_DN missing")

    user_filter = (settings.ldap_user_filter or "").format(username=username)
    ok = conn.search(search_base=base_dn, search_filter=user_filter, attributes=["distinguishedName", "memberOf"])
    if not ok or not conn.entries:
        conn.unbind()
        raise HTTPException(status_code=403, detail="Invalid credentials")
    if len(conn.entries) != 1:
        conn.unbind()
        raise HTTPException(status_code=403, detail="User lookup ambiguous")

    entry = conn.entries[0]
    user_dn = str(getattr(entry, "distinguishedName", "") or "").strip() or str(entry.entry_dn)
    groups = _extract_memberof(entry)

    # Validate credentials by binding as the user.
    user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
    user_conn.unbind()
    conn.unbind()

    g_admin = _normalize_dn(settings.ldap_group_dn_admin)
    g_911 = _normalize_dn(settings.ldap_group_dn_911)
    g_kc = _normalize_dn(settings.ldap_group_dn_kc)
    groups_norm = {_normalize_dn(g) for g in groups}

    role = None
    if g_admin and g_admin in groups_norm:
        role = ROLE_ADMIN
    elif g_911 and g_911 in groups_norm:
        role = ROLE_911
    elif g_kc and g_kc in groups_norm:
        role = ROLE_KC

    if not role:
        raise HTTPException(status_code=403, detail="No allowed AD groups for access")

    return UiIdentity(username=username, role=role, groups=groups)


def try_superuser_login(*, username: str, password: str) -> UiIdentity | None:
    """
    If UI_SUPERUSER_LOGIN / UI_SUPERUSER_PASSWORD are set in .env and match
    the submitted credentials, grant full ADMIN role (same as API admin key).
    """
    login = (settings.ui_superuser_login or "").strip()
    pwd = settings.ui_superuser_password or ""
    if not login or not pwd:
        return None
    if secrets.compare_digest(username.strip(), login) and secrets.compare_digest(password, pwd):
        return UiIdentity(username=username.strip(), role=ROLE_ADMIN, groups=["ui:superuser"])
    return None


def ui_login_authenticate(*, username: str, password: str) -> UiIdentity:
    """
    UI login order: superuser (env) → LDAP.
    """
    su = try_superuser_login(username=username, password=password)
    if su is not None:
        return su
    if not (settings.ldap_url or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LDAP is not configured. Set LDAP_URL or enable UI_SUPERUSER_LOGIN in .env for development.",
        )
    return ldap_authenticate_and_resolve_role(username=username.strip(), password=password)


def get_current_identity_ui(request: Request) -> UiIdentity:
    session = getattr(request, "session", None) or {}
    username = str(session.get("username") or "").strip()
    role = str(session.get("role") or "").strip()
    groups = session.get("groups") if isinstance(session.get("groups"), list) else []
    if not username or not role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return UiIdentity(username=username, role=role, groups=[str(g) for g in groups])


def get_current_role_ui(request: Request) -> str:
    return get_current_identity_ui(request).role