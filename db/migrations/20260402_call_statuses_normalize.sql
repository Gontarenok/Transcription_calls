-- Справочник статусов звонка (FK из calls) + перенос данных из legacy-колонки calls.status.
--
-- Порядок: выполнять после db/migrations/20260401_reset_call_classifications_and_kc_status.sql
-- (если тот применялся): сначала правится varchar status, затем эта миграция переносит в call_statuses.
--
-- Применение:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260402_call_statuses_normalize.sql
--
-- Коды статусов (единый набор для пайплайнов и UI):
--   NEW, TRANSCRIBING, TRANSCRIBED, TRANSCRIPTION_FAILED,
--   CLASSIFYING, CLASSIFIED, CLASSIFICATION_FAILED,
--   SUMMARIZING, SUMMARIZED, SUMMARIZATION_FAILED,
--   FAILED — legacy-общий сбой (старый KC-скрипт); для новых ошибок транскрибации предпочтителен TRANSCRIPTION_FAILED.

BEGIN;

CREATE TABLE IF NOT EXISTS call_statuses (
    id SERIAL PRIMARY KEY,
    code VARCHAR(30) NOT NULL,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_call_statuses_code UNIQUE (code)
);

CREATE INDEX IF NOT EXISTS ix_call_statuses_code ON call_statuses (code);

INSERT INTO call_statuses (code, name, created_at, deleted_at, is_active) VALUES
    ('NEW', 'Новый', NOW(), NULL, TRUE),
    ('TRANSCRIBING', 'Транскрибация', NOW(), NULL, TRUE),
    ('TRANSCRIBED', 'Транскрибирован', NOW(), NULL, TRUE),
    ('TRANSCRIPTION_FAILED', 'Ошибка транскрибации', NOW(), NULL, TRUE),
    ('CLASSIFYING', 'Классификация', NOW(), NULL, TRUE),
    ('CLASSIFIED', 'Классифицирован', NOW(), NULL, TRUE),
    ('CLASSIFICATION_FAILED', 'Ошибка классификации', NOW(), NULL, TRUE),
    ('SUMMARIZING', 'Саммаризация', NOW(), NULL, TRUE),
    ('SUMMARIZED', 'Саммаризирован', NOW(), NULL, TRUE),
    ('SUMMARIZATION_FAILED', 'Ошибка саммаризации', NOW(), NULL, TRUE),
    ('FAILED', 'Сбой (legacy)', NOW(), NULL, TRUE)
ON CONFLICT (code) DO NOTHING;

ALTER TABLE calls ADD COLUMN IF NOT EXISTS status_id INTEGER REFERENCES call_statuses (id);

UPDATE calls AS c
SET status_id = cs.id
FROM call_statuses AS cs
WHERE c.status_id IS NULL
  AND c.status = cs.code;

-- Неизвестные или пустые значения — в NEW (при появлении новых кодов расширяйте справочник и повторите UPDATE при необходимости)
UPDATE calls
SET status_id = (SELECT id FROM call_statuses WHERE code = 'NEW' LIMIT 1)
WHERE status_id IS NULL;

ALTER TABLE calls ALTER COLUMN status_id SET NOT NULL;

DROP INDEX IF EXISTS ix_calls_status;

ALTER TABLE calls DROP COLUMN IF EXISTS status;

CREATE INDEX IF NOT EXISTS ix_calls_status_id ON calls (status_id);

-- Типичный фильтр UI/API: тип + статус + сортировка по дате
CREATE INDEX IF NOT EXISTS ix_calls_call_type_status_started ON calls (call_type_id, status_id, call_started_at DESC);

COMMIT;
