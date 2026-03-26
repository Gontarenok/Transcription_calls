-- Нормализация справочника call_types и исторических данных.
-- Цель:
-- 1) гарантировать наличие 2 записей call_types: 911 и КЦ (КЦ строго кириллицей)
-- 2) устранить legacy код "KЦ" (латинская K) и перекинуть все calls на "КЦ"
--
-- Важно: миграция не удаляет данные calls/transcriptions и т.д., только нормализует call_type_id.

BEGIN;

-- 1) Upsert 911
INSERT INTO call_types (code, name, description, created_at, updated_at, deleted_at, is_active)
VALUES ('911', 'Внутренняя техническая поддержка', 'Звонки внутренней технической поддержки', NOW(), NOW(), NULL, TRUE)
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 2) Ensure canonical "КЦ"
INSERT INTO call_types (code, name, description, created_at, updated_at, deleted_at, is_active)
VALUES ('КЦ', 'Контакт-центр', 'Звонки в контакт-центр компании', NOW(), NOW(), NULL, TRUE)
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 3) Normalize legacy "KЦ" -> "КЦ"
DO $$
DECLARE
  id_kc_cyr int;
  id_kc_lat int;
BEGIN
  SELECT id INTO id_kc_cyr FROM call_types WHERE code = 'КЦ';
  SELECT id INTO id_kc_lat FROM call_types WHERE code = 'KЦ';

  IF id_kc_lat IS NULL THEN
    -- nothing to do
    RETURN;
  END IF;

  IF id_kc_cyr IS NULL THEN
    -- If canonical somehow not present, rename legacy row in-place
    UPDATE call_types
    SET code = 'КЦ', updated_at = NOW()
    WHERE id = id_kc_lat;
    RETURN;
  END IF;

  -- Re-link calls to canonical "КЦ"
  UPDATE calls
  SET call_type_id = id_kc_cyr
  WHERE call_type_id = id_kc_lat;

  -- Remove legacy row to satisfy unique constraint and avoid future ambiguity
  DELETE FROM call_types WHERE id = id_kc_lat;
END $$;

COMMIT;

