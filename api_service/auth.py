from fastapi import Header, HTTPException, Query, status

from api_service.config import settings

ROLE_911 = "911"
ROLE_KC = "KЦ"
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
        return {"KЦ"}
    return {"911", "KЦ"}