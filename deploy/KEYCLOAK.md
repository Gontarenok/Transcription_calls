# Авторизация: AD → Keycloak → OAuth2-Proxy → приложение

Схема прода:

```
браузер ──HTTPS──▶ nginx ──auth_request──▶ oauth2-proxy ──OIDC──▶ Keycloak
                     │                         │                      │
                     │                         │                      ▼
                     │                         │                 LDAP federation
                     │                         │                      │
                     │                         │                      ▼
                     │                         │                  Active Directory
                     │                         │                  (группы уже есть)
                     └─ X-Auth-Request-* ◀─────┘
                     │
                     ▼
                FastAPI (app)
```

Пользователи и группы живут в **AD** (корп. домен, OU `Transcription_calls`).
Keycloak забирает их через LDAP federation. Приложение не ходит в AD/Keycloak —
читает логин и список AD-групп из HTTP-заголовков, которые выставляет OAuth2-Proxy.

## Используемые AD-группы

Настраиваются в приложении через `TRUSTED_GROUP_*` (`.env`):

| AD-группа | Роль в приложении |
|---|---|
| `AG-AI calls-Administrators` | ADMIN: оба типа звонков, пайплайны, API ADMIN |
| `FG-AI calls CC-Users` | КЦ: звонки КЦ, классификация |
| `FG-AI calls CC directory-Users` | КЦ + редактирование справочника тем |
| `FG-AI calls 911-Users` | 911: звонки и саммаризация 911 |

## 1. Keycloak

### 1.1 LDAP federation с AD (если ещё не настроена)

Realm → **User federation → Add provider → ldap**:

| Поле | Значение |
|---|---|
| Vendor | **Active Directory** |
| Connection URL | `ldap://dc.corp.local:389` (или `ldaps://dc.corp.local:636`) |
| Users DN | `OU=Users,DC=corp,DC=local` (ваш реальный OU) |
| Bind DN | `CN=svc_keycloak,OU=Service,DC=corp,DC=local` |
| Bind credential | пароль сервисной учётки (read-only достаточно) |
| Edit mode | **READ_ONLY** |
| Username LDAP attribute | `sAMAccountName` |
| Import Users | **On** |
| Sync Registrations | **Off** |

Сохранить → **Synchronize all users**. Пользователи AD появятся в Users.

### 1.2 Подтянуть группы AD в Keycloak

В federation-провайдере → вкладка **Mappers → Add mapper**:

| Поле | Значение |
|---|---|
| Name | `groups-from-ad` |
| Mapper type | **group-ldap-mapper** |
| LDAP Groups DN | `OU=Transcription_calls,OU=SecLevel 2,OU=_mpGroup,DC=corp,DC=local` (OU, где лежат 4 группы выше) |
| Group Name LDAP Attribute | `cn` |
| Group Object Classes | `group` |
| Preserve Group Inheritance | **Off** (если иерархии групп нет) |
| Membership LDAP Attribute | `member` |
| Membership Attribute Type | **DN** |
| User Groups Retrieve Strategy | **LOAD_GROUPS_BY_MEMBER_ATTRIBUTE** |
| Mode | **READ_ONLY** |
| Drop non-existing groups during sync | **On** |

Сохранить → **Sync LDAP Groups to Keycloak**. В Keycloak → **Groups** появятся все 4 группы.

### 1.3 Client `transcription-calls`

Realm → **Clients → Create client**:

- Client type: **OpenID Connect**
- Client ID: `transcription-calls`
- Client authentication: **On** (confidential)
- Authentication flow: **Standard flow** (authorization code)

После создания:
- **Settings → Access settings**:
  - Valid redirect URIs: `https://calls.example.com/oauth2/callback`
  - Web origins: `https://calls.example.com`
- **Credentials** → скопировать **Client Secret** (пригодится для OAuth2-Proxy).

### 1.4 Маппер «группы в claim groups»

Клиент `transcription-calls` → **Client scopes** → `transcription-calls-dedicated`
→ **Add mapper → By configuration → Group Membership**:

| Поле | Значение |
|---|---|
| Name | `groups-claim` |
| Token Claim Name | `groups` |
| **Full group path** | **Off** (важно! без этого придут `/AG-AI calls-Administrators`) |
| Add to ID token | **On** |
| Add to access token | **On** |
| Add to userinfo | **On** |

После этого ID/Access/Userinfo токен будет содержать:
```json
{
  "preferred_username": "ivanov.ii",
  "groups": ["AG-AI calls-Administrators", "FG-AI calls CC-Users"]
}
```

### 1.5 Права пользователей

Пользователям уже назначены AD-группы — дополнительно в Keycloak ничего делать не нужно.
Можно проверить в Keycloak: Users → выбрать пользователя → **Groups** → там должны
быть `AG-AI calls-...`.

## 2. OAuth2-Proxy

### 2.1 Конфиг `/etc/oauth2-proxy/oauth2-proxy.cfg`

```toml
http_address = "127.0.0.1:4180"
upstreams = ["http://127.0.0.1:5000/"]
reverse_proxy = true

# OIDC провайдер — Keycloak
provider = "keycloak-oidc"
oidc_issuer_url = "https://keycloak.example.com/realms/MY_REALM"
client_id = "transcription-calls"
client_secret = "<КЛИЕНТСКИЙ СЕКРЕТ ИЗ KEYCLOAK>"
redirect_url = "https://calls.example.com/oauth2/callback"

# Из какого claim брать группы (мы смапили туда AD-группы)
oidc_groups_claim = "groups"

# Cookie
cookie_secret = "<python -c 'import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'>"
cookie_secure = true
cookie_domains = ["calls.example.com"]
cookie_samesite = "lax"
cookie_expire = "12h"

# Заголовки в апстрим
set_xauthrequest = true         # X-Auth-Request-User, -Email, -Preferred-Username, -Groups
pass_user_headers = true
pass_access_token = false

# Авторизацию по группам делает приложение; здесь пропускаем всех,
# кого аутентифицировал Keycloak
email_domains = ["*"]

# Белый список redirect после логина
whitelist_domains = [".example.com"]
```

### 2.2 systemd unit `/etc/systemd/system/oauth2-proxy.service`

```ini
[Unit]
Description=OAuth2 Proxy
After=network.target

[Service]
Type=simple
User=oauth2-proxy
Group=oauth2-proxy
Restart=always
ExecStart=/usr/local/bin/oauth2-proxy --config=/etc/oauth2-proxy/oauth2-proxy.cfg

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oauth2-proxy
```

## 3. nginx

`/etc/nginx/sites-available/transcription-calls`:

```nginx
server {
    listen 443 ssl http2;
    server_name calls.example.com;

    ssl_certificate     /etc/letsencrypt/live/calls.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/calls.example.com/privkey.pem;

    # 1. Эндпоинты oauth2-proxy (login/logout/callback)
    location /oauth2/ {
        proxy_pass http://127.0.0.1:4180;
        proxy_set_header Host                    $host;
        proxy_set_header X-Real-IP               $remote_addr;
        proxy_set_header X-Forwarded-For         $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto       $scheme;
    }

    # 2. Внутренний check
    location = /oauth2/auth {
        internal;
        proxy_pass http://127.0.0.1:4180;
        proxy_set_header Host             $host;
        proxy_set_header X-Real-IP        $remote_addr;
        proxy_set_header X-Forwarded-For  $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Uri  $request_uri;
        proxy_pass_request_body off;
        proxy_set_header Content-Length   "";
    }

    # 3. Защищённое приложение
    location / {
        auth_request /oauth2/auth;
        error_page 401 = /oauth2/sign_in;

        # oauth2-proxy сам проксирует на upstream (http://127.0.0.1:5000)
        # и уже вложил X-Auth-Request-* в запрос.
        proxy_pass http://127.0.0.1:4180;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 4. Flower (мониторинг Celery) — под тем же SSO
    location /flower/ {
        auth_request /oauth2/auth;
        error_page 401 = /oauth2/sign_in;
        proxy_pass http://127.0.0.1:5555/;
    }
}
```

## 4. `.env` на проде (готовый блок)

```dotenv
UI_AUTH_MODE=trusted_headers
UI_SUPERUSER_ENABLED=0
SESSION_COOKIE_SECURE=1

# Заголовки от OAuth2-Proxy (без переименования в nginx)
TRUSTED_HEADER_LOGIN=X-Auth-Request-Preferred-Username
TRUSTED_HEADER_ROLES=X-Auth-Request-Roles
TRUSTED_HEADER_GROUPS=X-Auth-Request-Groups

# Keycloak отдаёт AD-группы в X-Auth-Request-Groups; используем сопоставление по группам
TRUSTED_PREFER_ROLES_HEADER=0

TRUSTED_GROUP_ADMIN=AG-AI calls-Administrators
TRUSTED_GROUP_KC=FG-AI calls CC-Users
TRUSTED_GROUP_KC_CATALOG=FG-AI calls CC directory-Users
TRUSTED_GROUP_911=FG-AI calls 911-Users
```

## 5. Проверка и диагностика

### 5.1 Токен Keycloak содержит нужный claim

```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  https://keycloak.example.com/realms/MY_REALM/protocol/openid-connect/userinfo | jq
```
Ожидаемый ответ:
```json
{
  "preferred_username": "ivanov.ii",
  "groups": ["AG-AI calls-Administrators", "FG-AI calls CC-Users"]
}
```

### 5.2 OAuth2-Proxy отдаёт заголовки в приложение

Можно временно добавить в `api_service/main.py`:
```python
@app.get("/debug/whoami")
def whoami(request: Request):
    return {k: v for k, v in request.headers.items() if k.lower().startswith("x-")}
```

Через браузер (с авторизацией) открыть `/debug/whoami` — должны увидеть:
```
x-auth-request-preferred-username: ivanov.ii
x-auth-request-groups: AG-AI calls-Administrators,FG-AI calls CC-Users
x-auth-request-email: ivanov.ii@corp.local
```

### 5.3 Типичные ошибки

| Симптом | Причина | Решение |
|---|---|---|
| 401 на каждой странице, в редиректе Keycloak заходит, но обратно — 401 | В `/debug/whoami` нет `x-auth-request-preferred-username` | Проверить `set_xauthrequest = true` в oauth2-proxy.cfg |
| «Нет заголовка X-Forwarded-Roles / X-Forwarded-Groups» | `TRUSTED_HEADER_GROUPS` указывает на несуществующий заголовок | В `.env` должно быть `TRUSTED_HEADER_GROUPS=X-Auth-Request-Groups` |
| «Нет прав на доступ (группы/роли не сопоставлены)» | `x-auth-request-groups` приходит, но содержимое не матчится с `TRUSTED_GROUP_*` | Сверить имена AD-групп с .env; если идёт `/AG-AI ...` (с косой чертой) — в Group Membership mapper **Full group path** должен быть **Off** |
| Токен Keycloak содержит `"groups": []` | В federation-маппере не синхронизированы LDAP-группы либо пользователю не вычисляются memberOf | В User federation → `groups-from-ad` → **Sync LDAP Groups**; проверить Users → Groups в UI Keycloak |
