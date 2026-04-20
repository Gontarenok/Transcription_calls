# Деплой на Linux-прод — инструкция для девопса

Архитектура:

```
             ┌──────────────────────────────────────┐
             │             Prod Linux host          │
             │  ┌──────────────────────────────┐    │
   HTTPS ──▶ │  │ nginx  (TLS, auth_request)   │    │
             │  └────┬────────┬────────────────┘    │
             │       │        │                     │
             │       ▼        ▼ (X-Auth-Request-*)  │
             │  ┌─────────┐   │                     │
             │  │oauth2-  │   │                     │
             │  │proxy    │   │                     │
             │  └────┬────┘   │                     │
             │       │        ▼                     │
             │       │   ┌──────────────────────┐   │
             │       │   │ docker compose       │   │
             │       │   │ ├─ web (FastAPI)     │   │
             │       │   │ ├─ worker-transcribe │   │
             │       │   │ ├─ worker-light      │   │
             │       │   │ ├─ flower            │   │
             │       │   │ └─ redis             │   │
             │       │   └──────────────────────┘   │
             │       │                              │
             │  /srv/audio       (сеть NAS/SMB)     │
             │  /srv/ai-models   (локально)         │
             └───────┼──────────┬───────────┬───────┘
                     ▼          ▼           ▼
                 Keycloak  PostgreSQL   Qdrant
                 (+ AD)    (внешний)   (внешний)
```

## 0. Предусловия (вне приложения)

- **PostgreSQL** — уже развёрнут отдельно. Создать БД `audio_calls`, пользователя с правами на эту БД. URL:
  `postgresql+psycopg2://user:pass@<host>:5432/audio_calls`.
- **Qdrant** — уже развёрнут отдельно. URL + API-ключ.
- **Сетевая шара со звонками** — примонтирована на прод-хост (NAS/CIFS/NFS), доступна как `/srv/audio/...`.
- **Сервер** — Ubuntu 22.04 LTS (или Debian 12 / AlmaLinux 9 — команды `apt` заменятся на `dnf`).
- **GitLab Deploy Token** — в проекте GitLab: *Settings → Repository → Deploy tokens*. Scope: `read_registry`. Сохранить username и value — пойдут в CI переменные `CI_DEPLOY_USER` / `CI_DEPLOY_PASSWORD`.

## 1. Подготовка сервера (один раз)

```bash
# 1.1 Технический пользователь
sudo adduser --disabled-password --gecos "" deploy
sudo mkdir -p /home/deploy/.ssh && sudo chmod 700 /home/deploy/.ssh
# Положить публичный ключ (парный к SSH_PRIVATE_KEY в GitLab CI):
sudo tee /home/deploy/.ssh/authorized_keys > /dev/null <<'EOF'
<PUBLIC-KEY>
EOF
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys

# 1.2 Docker Engine + Compose v2
sudo apt update && sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy

# 1.3 Каталоги и права
sudo mkdir -p /opt/transcription-calls /srv/ai-models /srv/audio
sudo chown -R deploy:deploy /opt/transcription-calls /srv/ai-models
# /srv/audio монтируется отдельно из NAS (cifs-utils + /etc/fstab)
```

## 2. Разложить репозиторий и .env

```bash
sudo -u deploy -H bash <<'EOF'
cd /opt/transcription-calls
git clone https://gitlab.example.com/<group>/<repo>.git .
# Dev-override в прод НЕ нужен (на проде compose должен тянуть image, а не собирать):
rm -f docker-compose.override.yml
cp .env.example .env
EOF
sudo chmod 600 /opt/transcription-calls/.env
sudo -u deploy nano /opt/transcription-calls/.env
```

Обязательно заполнить в `.env`:

| Ключ | Значение |
|---|---|
| `DATABASE_URL` | внешний Postgres |
| `QDRANT_URL`, `QDRANT_API`, `QDRANT_COLLECTION_NAME` | внешний Qdrant |
| `REDIS_PASSWORD` | длинный случайный пароль |
| `API_KEY_911`, `API_KEY_KC`, `API_KEY_ADMIN` | секреты для JSON API |
| `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLOWER_BASIC_AUTH` | `логин:пароль` |
| `N911_AUDIO_ROOT`, `KC_AUDIO_ROOT` | пути **внутри контейнера** (например `/srv/audio/911`, `/srv/audio/kc`) |
| `AUDIO_HOST_PATH`, `MODELS_HOST_PATH` | хостовые пути (`/srv/audio`, `/srv/ai-models`) |
| `WHISPER_MODELS_ROOT`, `GEMMA_MODEL_PATH`, `EMBEDDING_MODEL_*_PATH` | пути **внутри контейнера** (`/srv/ai-models/...`) |
| `UI_AUTH_MODE=trusted_headers`, `UI_SUPERUSER_ENABLED=0` | прод |

## 3. Скачать модели (один раз)

Требование проекта — использовать локальные модели. После установки прокидываем их в volume.

```bash
sudo -u deploy -H bash <<'EOF'
cd /opt/transcription-calls
# Пытаемся скачать в /srv/ai-models через Python-окружение образа
# (после первого docker login + docker compose pull, см. шаг 4).
# Либо вручную на любой машине с интернетом и rsync-нуть в /srv/ai-models.
docker run --rm -v /srv/ai-models:/srv/ai-models \
  -e HF_TOKEN="$HF_TOKEN" \
  python:3.11-slim bash -c \
    "pip install --no-cache-dir openai-whisper huggingface_hub sentence-transformers && \
     python - <<'PY'
import os
from pathlib import Path
import whisper
from huggingface_hub import snapshot_download

root = Path('/srv/ai-models')
whisper.load_model('medium', download_root=str(root / 'whisper'))
whisper.load_model('large-v3', download_root=str(root / 'whisper'))
snapshot_download('google/gemma-3-4b-it', local_dir=str(root / 'gemma' / 'gemma-3-4b-it'),
                  local_dir_use_symlinks=False, token=os.getenv('HF_TOKEN'))
snapshot_download('BAAI/bge-m3', local_dir=str(root / 'embeddings' / 'bge-m3'),
                  local_dir_use_symlinks=False, token=os.getenv('HF_TOKEN'))
PY"
EOF
```

Альтернатива: запустить `python scripts/download_models.py --target /srv/ai-models --all` из контейнера `web` после первого `docker compose pull`.

В `.env` после скачивания:
```
WHISPER_MODELS_ROOT=/srv/ai-models/whisper
GEMMA_MODEL_PATH=/srv/ai-models/gemma/gemma-3-4b-it
EMBEDDING_MODEL_MINI_PATH=/srv/ai-models/embeddings/bge-m3
EMBEDDING_MODEL_SBER_PATH=/srv/ai-models/embeddings/sbert_large_nlu_ru
```

## 4. Первый ручной pull + запуск

```bash
sudo -u deploy -H bash <<'EOF'
cd /opt/transcription-calls
# Залогиниться в GitLab CR (deploy-токеном)
docker login registry.gitlab.com -u <CI_DEPLOY_USER> -p <CI_DEPLOY_PASSWORD>

# Записать текущий тег (в дальнейшем это делает CI автоматически)
cat > .image.env <<EOT
IMAGE_NAME=registry.gitlab.com/<group>/<repo>/transcription-calls
IMAGE_TAG=latest
EOT

docker compose --env-file .env --env-file .image.env pull
docker compose --env-file .env --env-file .image.env run --rm web \
  python -c "from db.init_db import create_all; create_all()"
docker compose --env-file .env --env-file .image.env up -d
docker compose ps
EOF
```

## 5. Авторизация, nginx + HTTPS

В проде авторизация идёт по схеме: **браузер → nginx → OAuth2-Proxy → Keycloak → приложение**.
Приложение читает имя пользователя и роли из HTTP-заголовков в режиме `UI_AUTH_MODE=trusted_headers`.

Подробная пошаговая настройка Keycloak (клиент, realm roles, маппер), OAuth2-Proxy и nginx —
в отдельном документе: **[deploy/KEYCLOAK.md](KEYCLOAK.md)**.

Кратко:
- `oauth2-proxy` работает как systemd-сервис на `127.0.0.1:4180`, проксирует на `127.0.0.1:5000`.
- nginx терминирует TLS и через `auth_request /oauth2/auth` защищает все пути.
- В `.env` на проде (уже есть в `.env.example`): `TRUSTED_HEADER_LOGIN=X-Auth-Request-Preferred-Username`,
  `TRUSTED_HEADER_GROUPS=X-Auth-Request-Groups`, `TRUSTED_PREFER_ROLES_HEADER=0`.
- В Keycloak нужна LDAP federation с AD (с group-ldap-mapper, чтобы AD-группы
  попали в Keycloak) и **Group Membership mapper** на клиенте с Token Claim Name = `groups`,
  иначе OAuth2-Proxy не получит список групп. AD-группы при этом остаются с реальными
  именами (`AG-AI calls-Administrators` и т.д.) и матчатся в приложении по `TRUSTED_GROUP_*`.

## 6. Таймеры периодических пайплайнов

```bash
sudo cp /opt/transcription-calls/deploy/systemd/*.service /etc/systemd/system/
sudo cp /opt/transcription-calls/deploy/systemd/*.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
  transcription-calls-911.timer \
  transcription-calls-kc.timer \
  transcription-calls-classify.timer \
  transcription-calls-summarize911.timer
```

Проверка: `systemctl list-timers`, `journalctl -u transcription-calls-911.service -f`.

## 7. GitLab CI/CD Variables

В проекте GitLab: **Settings → CI/CD → Variables** (всё Protected + Masked):

| Variable | Value |
|---|---|
| `SSH_PRIVATE_KEY` | приватный ключ для `deploy@host` (ed25519) |
| `SSH_KNOWN_HOSTS` | `ssh-keyscan -H <DEPLOY_HOST>` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_HOST` | IP/DNS прод-сервера |
| `DEPLOY_PATH` | `/opt/transcription-calls` |
| `CI_DEPLOY_USER` | username deploy-токена GitLab |
| `CI_DEPLOY_PASSWORD` | value deploy-токена GitLab |

## 8. Ежедневный деплой

После пуша в `main`:
1. GitLab CI запускает `test` (Postgres service, миграция, verify).
2. `build` — собирает образ `registry/.../transcription-calls:<SHA>`, пушит, а также `latest` (только с main).
3. `deploy_prod` по SSH на проде:
   - пишет `.image.env` с новым `IMAGE_TAG=<SHA>`;
   - `docker login` → `docker compose pull`;
   - `create_all` (идемпотентно);
   - `docker compose up -d --remove-orphans` (перезапускает только изменённые контейнеры);
   - `docker image prune -f`.

## 9. Откат

Если деплой сломал прод — откатить к предыдущему тегу:
```bash
cd /opt/transcription-calls
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=<PREV_SHA>/' .image.env
docker compose --env-file .env --env-file .image.env pull
docker compose --env-file .env --env-file .image.env up -d
```

## 10. Бэкапы

В `docs/BACKUP.md` политика. Добавить `/etc/cron.daily/pg-backup-transcription` со снапом внешнего Postgres
(если есть доступ с прод-сервера) или настроить бэкапы на самом Postgres-хосте.

## Частые операции

```bash
docker compose ps
docker compose logs -f --tail=200 web
docker compose logs -f --tail=200 worker-transcribe
docker compose restart web
docker compose exec web bash                     # зайти внутрь
docker compose exec web python scripts/enqueue.py classify --limit 50

systemctl list-timers
journalctl -u transcription-calls-911.service -n 200

docker system df
docker system prune -af                          # осторожно
```
