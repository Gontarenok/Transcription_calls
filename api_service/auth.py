from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Header, HTTPException, Request, status
from ldap3 import ALL, Connection, Server

from api_service.config import settings

ROLE_911 = "911"
ROLE_KC = "КЦ"
ROLE_ADMIN = "ADMIN"
# Редактор справочника КЦ (без логов пайплайнов и без полного ADMIN API по ключам)
ROLE_KC_CATALOG = "КЦ_СПРАВОЧНИК"
ROLE_MIXED_UI = "911+КЦ"


@dataclass
class UiIdentity:
    username: str
    role: str
    groups: list[str]
    call_types: set[str]
    catalog_access: bool
    pipeline_admin: bool

    @classmethod
    def from_single_role(cls, username: str, role: str, groups: list[str]) -> UiIdentity:
        ct = set(allowed_call_types_for_role(role))
        if not ct:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Неизвестная или недопустимая роль сессии",
            )
        return cls(
            username=username,
            role=role,
            groups=list(groups),
            call_types=ct,
            catalog_access=role in {ROLE_ADMIN, ROLE_KC_CATALOG},
            pipeline_admin=role == ROLE_ADMIN,
        )


def allowed_call_types_for_role(role: str) -> set[str]:
    if role == ROLE_ADMIN:
        return {"911", "КЦ"}
    if role == ROLE_911:
        return {"911"}
    if role == ROLE_KC:
        return {"КЦ"}
    if role == ROLE_KC_CATALOG:
        return {"КЦ"}
    if role == ROLE_MIXED_UI:
        return {"911", "КЦ"}
    return set()


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
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    key = x_api_key
    role = resolve_role_by_key(key)
    if role:
        return role
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def identity_sees_kc_classification(identity: UiIdentity) -> bool:
    return "КЦ" in identity.call_types


def _split_header_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        t = chunk.strip()
        if t:
            out.append(t)
    return out


def _marker_in_token(marker: str, token: str) -> bool:
    if not marker or not token:
        return False
    m, t = marker.casefold().strip(), token.casefold().strip()
    return m in t or t in m


def _roles_from_tokens(tokens: list[str]) -> tuple[bool, bool, bool, bool]:
    """Возвращает флаги: is_admin, is_kc_catalog, is_kc, is_911 по заголовку ролей."""
    norms = {t.casefold().strip() for t in tokens}
    is_admin = settings.trusted_role_admin.casefold().strip() in norms
    is_kc_cat = settings.trusted_role_kc_catalog.casefold().strip() in norms
    is_kc = settings.trusted_role_kc.casefold().strip() in norms
    is_911 = settings.trusted_role_911.casefold().strip() in norms
    return is_admin, is_kc_cat, is_kc, is_911


def _groups_to_flags(tokens: list[str]) -> tuple[bool, bool, bool, bool]:
    is_admin = any(_marker_in_token(settings.trusted_group_admin, t) for t in tokens)
    is_kc_cat = any(_marker_in_token(settings.trusted_group_kc_catalog, t) for t in tokens)
    is_kc = any(_marker_in_token(settings.trusted_group_kc, t) for t in tokens)
    is_911 = any(_marker_in_token(settings.trusted_group_911, t) for t in tokens)
    return is_admin, is_kc_cat, is_kc, is_911


def assemble_ui_identity(
    *,
    username: str,
    groups_for_display: list[str],
    is_admin: bool,
    is_kc_catalog: bool,
    is_kc: bool,
    is_911: bool,
) -> UiIdentity:
    if is_admin:
        call_types: set[str] = {"911", "КЦ"}
    else:
        call_types = set()
        if is_911:
            call_types.add("911")
        if is_kc or is_kc_catalog:
            call_types.add("КЦ")

    catalog_access = is_admin or is_kc_catalog
    pipeline_admin = is_admin

    if not call_types:
        raise HTTPException(status_code=403, detail="Нет прав на доступ (группы/роли не сопоставлены)")

    if is_admin:
        display_role = ROLE_ADMIN
    elif is_kc_catalog:
        display_role = ROLE_KC_CATALOG
    elif call_types == {"911", "КЦ"}:
        display_role = ROLE_MIXED_UI
    elif "КЦ" in call_types:
        display_role = ROLE_KC
    else:
        display_role = ROLE_911

    return UiIdentity(
        username=username.strip(),
        role=display_role,
        groups=list(groups_for_display),
        call_types=call_types,
        catalog_access=catalog_access,
        pipeline_admin=pipeline_admin,
    )


def identity_from_trusted_headers(request: Request) -> UiIdentity:
    login_key = settings.trusted_header_login
    raw_login = request.headers.get(login_key) or request.headers.get(login_key.title())
    username = (raw_login or "").strip()
    if not username:
        raise HTTPException(status_code=401, detail="Нет учётных данных в заголовке (ожидается X-Forwarded-Login)")

    roles_header = request.headers.get(settings.trusted_header_roles) or request.headers.get(
        settings.trusted_header_roles.title()
    )
    groups_header = request.headers.get(settings.trusted_header_groups) or request.headers.get(
        settings.trusted_header_groups.title()
    )

    roles_tokens = _split_header_list(roles_header)
    groups_tokens = _split_header_list(groups_header)

    if settings.trusted_prefer_roles_header and roles_tokens:
        flags = _roles_from_tokens(roles_tokens)
        g_for_display = roles_tokens
    elif groups_tokens:
        flags = _groups_to_flags(groups_tokens)
        g_for_display = groups_tokens
    elif roles_tokens:
        flags = _roles_from_tokens(roles_tokens)
        g_for_display = roles_tokens
    else:
        raise HTTPException(status_code=403, detail="Нет заголовка X-Forwarded-Roles / X-Forwarded-Groups")

    return assemble_ui_identity(
        username=username,
        groups_for_display=g_for_display,
        is_admin=flags[0],
        is_kc_catalog=flags[1],
        is_kc=flags[2],
        is_911=flags[3],
    )


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


def _ldap_member_matches(member_of: str, configured: str) -> bool:
    if not configured or not configured.strip():
        return False
    return _marker_in_token(configured.strip(), member_of)


def _ldap_open_and_bind(*, user_dn: str | None, password: str | None, invalid_credentials: bool = False) -> Connection:
    """ldap://: сначала соединение и StartTLS, затем bind. ldaps://: только bind."""
    if not settings.ldap_url:
        raise HTTPException(status_code=500, detail="LDAP is not configured (LDAP_URL missing)")
    use_ldaps = settings.ldap_url.lower().startswith("ldaps")
    server = Server(settings.ldap_url, get_info=ALL)
    conn = Connection(server, user=user_dn, password=password, auto_bind=False)
    conn.open()
    if not use_ldaps and settings.ldap_starttls:
        try:
            conn.start_tls()
        except Exception as exc:
            conn.unbind()
            raise HTTPException(status_code=500, detail=f"LDAP StartTLS failed: {exc}")
    if not conn.bind():
        conn.unbind()
        raise HTTPException(
            status_code=403 if invalid_credentials else 500,
            detail="Invalid credentials" if invalid_credentials else "LDAP bind failed",
        )
    return conn


def ldap_authenticate_and_resolve_role(*, username: str, password: str) -> UiIdentity:
    if not settings.ldap_url:
        raise HTTPException(status_code=500, detail="LDAP is not configured (LDAP_URL missing)")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username/password")

    conn = _ldap_open_and_bind(user_dn=settings.ldap_bind_dn or None, password=settings.ldap_bind_password or None)

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
    conn.unbind()

    # Проверка пароля пользователя — отдельное соединение с тем же порядком TLS.
    verify = _ldap_open_and_bind(user_dn=user_dn, password=password, invalid_credentials=True)
    verify.unbind()

    is_admin = any(_ldap_member_matches(g, settings.ldap_group_dn_admin) for g in groups)
    is_kc_cat = any(_ldap_member_matches(g, settings.ldap_group_dn_kc_catalog) for g in groups)
    is_kc = any(_ldap_member_matches(g, settings.ldap_group_dn_kc) for g in groups)
    is_911 = any(_ldap_member_matches(g, settings.ldap_group_dn_911) for g in groups)

    return assemble_ui_identity(
        username=username.strip(),
        groups_for_display=groups,
        is_admin=is_admin,
        is_kc_catalog=is_kc_cat,
        is_kc=is_kc,
        is_911=is_911,
    )


def try_superuser_login(*, username: str, password: str) -> UiIdentity | None:
    if not settings.ui_superuser_enabled:
        return None
    login = (settings.ui_superuser_login or "").strip()
    pwd = settings.ui_superuser_password or ""
    if not login or not pwd:
        return None
    if secrets.compare_digest(username.strip(), login) and secrets.compare_digest(password, pwd):
        return assemble_ui_identity(
            username=username.strip(),
            groups_for_display=["ui:superuser"],
            is_admin=True,
            is_kc_catalog=False,
            is_kc=False,
            is_911=False,
        )
    return None


def ui_login_authenticate(*, username: str, password: str) -> UiIdentity:
    su = try_superuser_login(username=username, password=password)
    if su is not None:
        return su
    if settings.ui_auth_mode == "trusted_headers":
        raise HTTPException(
            status_code=400,
            detail="Вход по форме отключён (trusted_headers). Включите UI_SUPERUSER_ENABLED=1 и задайте UI_SUPERUSER_LOGIN / UI_SUPERUSER_PASSWORD для локального суперпользователя.",
        )
    if not (settings.ldap_url or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LDAP is not configured. Set LDAP_*, switch UI_AUTH_MODE, or enable UI_SUPERUSER_LOGIN.",
        )
    return ldap_authenticate_and_resolve_role(username=username.strip(), password=password)


def identity_from_session(session: dict) -> UiIdentity:
    username = str(session.get("username") or "").strip()
    role = str(session.get("role") or "").strip()
    groups_raw = session.get("groups") if isinstance(session.get("groups"), list) else []
    groups = [str(g) for g in groups_raw]
    if not username or not role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    ct_raw = session.get("call_types")
    if isinstance(ct_raw, list) and ct_raw:
        return UiIdentity(
            username=username,
            role=role,
            groups=groups,
            call_types=set(str(x) for x in ct_raw),
            catalog_access=bool(session.get("catalog_access")),
            pipeline_admin=bool(session.get("pipeline_admin")),
        )
    return UiIdentity.from_single_role(username, role, groups)


def _trusted_proxy_login_present(request: Request) -> bool:
    k = settings.trusted_header_login
    return bool((request.headers.get(k) or request.headers.get(k.title()) or "").strip())


def get_current_identity_ui(request: Request) -> UiIdentity:
    if settings.ui_auth_mode == "trusted_headers":
        # Сначала прокси (прод): заголовки важнее сессии.
        if _trusted_proxy_login_present(request):
            return identity_from_trusted_headers(request)
        # Dev / аварийный вход: сессия суперпользователя, если разрешено.
        if settings.ui_superuser_enabled:
            session = getattr(request, "session", None) or {}
            if session.get("username") and session.get("role"):
                return identity_from_session(session)
        return identity_from_trusted_headers(request)

    session = getattr(request, "session", None) or {}
    return identity_from_session(session)


def get_current_role_ui(request: Request) -> str:
    return get_current_identity_ui(request).role


def menu_context(active: str, identity: UiIdentity) -> dict:
    return {
        "role": identity.role,
        "active": active,
        "catalog_access": identity.catalog_access,
        "pipeline_admin": identity.pipeline_admin,
        "can_see_classification": identity_sees_kc_classification(identity),
    }
