# Авторизация: Keycloak + OAuth2-Proxy + nginx

Схема:
```
браузер ──HTTPS──▶ nginx ──auth_request──▶ oauth2-proxy ──OIDC──▶ Keycloak
                     │                         │
                     └─ X-Auth-Request-* ◀─────┘
                     │
                     ▼
            FastAPI (app)
```

Приложение читает имя пользователя и роли из HTTP-заголовков, которые выставляет OAuth2-Proxy
(режим `UI_AUTH_MODE=trusted_headers`). См. `api_service/auth.py`.

## 1. Keycloak

### 1.1 Realm roles

Создать realm roles, имена которых совпадают со значениями `TRUSTED_ROLE_*` в `.env`:

- `admin`        → полный доступ (911 + КЦ + пайплайны + ADMIN API)
- `kc_cc`        → только КЦ-звонки и классификация
- `kc_catalog`   → КЦ + редактирование справочника тем
- `911`          → только 911-звонки и саммаризация

Если у вас в Keycloak другие имена (например `calls_admin`), пропишите их в `.env`:
```
TRUSTED_ROLE_ADMIN=calls_admin
TRUSTED_ROLE_KC=calls_cc
TRUSTED_ROLE_KC_CATALOG=calls_catalog
TRUSTED_ROLE_911=calls_911
```

### 1.2 Client `transcription-calls`

- Clients → Create:
  - Client type: **OpenID Connect**
  - Client ID: `transcription-calls`
  - Client authentication: **On** (confidential)
  - Authentication flow: **Standard flow** (authorization code)
  - Valid redirect URIs: `https://calls.example.com/oauth2/callback`
  - Web origins: `https://calls.example.com`
- После создания → Credentials → скопировать **Client Secret** (пойдёт в OAuth2-Proxy).

### 1.3 Маппер realm_access.roles → claim `groups`

Stock oauth2-proxy по умолчанию берёт группы из claim `groups` и кладёт в
`X-Auth-Request-Groups`. Нативно он **не извлекает `realm_access.roles`** — нужен маппер
в Keycloak, который продублирует realm roles в claim с именем `groups`.

Клиент `transcription-calls` → **Client scopes** → `transcription-calls-dedicated`
(или любой shared scope) → **Add mapper → By configuration → User Realm Role**:

| Поле | Значение |
|---|---|
| Name | `realm-roles-as-groups` |
| Multivalued | **On** |
| Token Claim Name | `groups` |
| Claim JSON Type | `String` |
| Add to ID token | **On** |
| Add to access token | **On** |
| Add to userinfo | **On** |

Проверить через UserInfo endpoint: токен должен содержать
```json
{
  "preferred_username": "ivanov.ii",
  "groups": ["admin", "kc_cc"]
}
```

### 1.4 Назначить роли пользователям

Users → выбрать пользователя → **Role mapping → Assign role** → realm roles.

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

# Из какого claim брать группы (мы смапили туда realm_access.roles)
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

# Авторизацию по ролям делает приложение; здесь пропускаем всех,
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
        # при 401 — на логин Keycloak через oauth2-proxy
        error_page 401 = /oauth2/sign_in;

        # Пробрасываем X-Auth-Request-* (пробросятся сами через proxy_pass)
        proxy_pass http://127.0.0.1:5000;
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

> Примечание: заголовки `X-Auth-Request-*` oauth2-proxy выставляет в ответе на `/oauth2/auth`,
> но чтобы они реально попали в апстрим приложения, нужно либо использовать oauth2-proxy как
> **upstream** (режим reverse_proxy), либо явно скопировать их через `auth_request_set`
> и `proxy_set_header`. В конфиге выше используется первый способ — проще.

Если выбираете вариант «переименовать заголовки в nginx» (чтобы оставить дефолтные
`X-Forwarded-Login` / `X-Forwarded-Roles` в `.env`), то location выше превращается в:

```nginx
location / {
    auth_request /oauth2/auth;
    error_page 401 = /oauth2/sign_in;

    auth_request_set $auth_user   $upstream_http_x_auth_request_preferred_username;
    auth_request_set $auth_groups $upstream_http_x_auth_request_groups;

    proxy_set_header X-Forwarded-Login  $auth_user;
    proxy_set_header X-Forwarded-Roles  $auth_groups;

    proxy_pass http://127.0.0.1:5000;
}
```

## 4. `.env` на проде

Выбираем один из двух вариантов.

### Вариант A — приложение читает X-Auth-Request-* напрямую (рекомендую)
```dotenv
UI_AUTH_MODE=trusted_headers
UI_SUPERUSER_ENABLED=0
SESSION_COOKIE_SECURE=1

TRUSTED_HEADER_LOGIN=X-Auth-Request-Preferred-Username
TRUSTED_HEADER_ROLES=X-Auth-Request-Groups
TRUSTED_HEADER_GROUPS=X-Auth-Request-Groups
TRUSTED_PREFER_ROLES_HEADER=1

TRUSTED_ROLE_ADMIN=admin
TRUSTED_ROLE_KC=kc_cc
TRUSTED_ROLE_KC_CATALOG=kc_catalog
TRUSTED_ROLE_911=911
```

### Вариант B — имена переименовываются в nginx
```dotenv
UI_AUTH_MODE=trusted_headers
UI_SUPERUSER_ENABLED=0
SESSION_COOKIE_SECURE=1

TRUSTED_HEADER_LOGIN=X-Forwarded-Login
TRUSTED_HEADER_ROLES=X-Forwarded-Roles
TRUSTED_PREFER_ROLES_HEADER=1
# TRUSTED_ROLE_* — как в варианте A
```

## 5. Диагностика

**Проверить, что токен содержит нужный claim:**
```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  https://keycloak.example.com/realms/MY_REALM/protocol/openid-connect/userinfo | jq
# должно быть: "groups": ["admin", ...]
```

**Посмотреть, какие заголовки реально доходят до приложения:**
Временно добавить в FastAPI endpoint:
```python
@app.get("/debug/whoami")
def whoami(request: Request):
    return dict(request.headers)
```
и зайти из браузера через nginx → oauth2-proxy — в ответе увидите все `X-Auth-Request-*`.

**401 на всех страницах:** `TRUSTED_HEADER_LOGIN` указывает на заголовок, которого нет.
Сверить с тем, что пришло в `/debug/whoami`.

**403 «Нет прав на доступ (группы/роли не сопоставлены)»:** `X-Auth-Request-Groups`
пустой или в нём имена ролей, не совпадающие с `TRUSTED_ROLE_*`. Проверить маппер в Keycloak
и значение `oidc_groups_claim` в oauth2-proxy.
