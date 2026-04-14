-- Reset test classification runs: empty call_classifications and return КЦ calls to TRANSCRIBED
--
-- Context:
-- - Scripts classification_rag/classify_calls.py and classification_rag/classify_calls_v2.py select calls with status IN
--   ('TRANSCRIBED', 'CLASSIFICATION_FAILED') by default (see db/crud.get_calls_for_classification).
-- - During a run they set status CLASSIFYING, then CLASSIFIED (or CLASSIFICATION_FAILED on error).
--
-- This migration:
-- 1) Removes ALL rows from call_classifications (hard delete; sequences reset).
-- 2) For calls of type КЦ (and legacy codes KЦ/KC if still present), sets status back to TRANSCRIBED
--    if they were left in CLASSIFIED, CLASSIFICATION_FAILED, or CLASSIFYING (e.g. interrupted run).
--
-- NOT touched:
-- - transcriptions, topic_catalog_entries, Qdrant
-- - calls.pipeline_run_id (usually last transcription pipeline; classification does not overwrite it)
-- - pipeline_runs (optional cleanup below)
-- - 911 calls and non-classification statuses (SUMMARIZED, etc.)
--
-- Apply:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20260401_reset_call_classifications_and_kc_status.sql
--
-- To also remove only classification pipeline logs (optional), uncomment the block at the end.

BEGIN;

TRUNCATE TABLE call_classifications RESTART IDENTITY;

UPDATE calls AS c
SET
    status = 'TRANSCRIBED',
    error_message = NULL
FROM call_types AS ct
WHERE c.call_type_id = ct.id
  AND ct.code IN ('КЦ', 'KЦ', 'KC')
  AND c.status IN ('CLASSIFIED', 'CLASSIFICATION_FAILED', 'CLASSIFYING');

COMMIT;

-- ---------------------------------------------------------------------------
-- После миграции db/migrations/20260402_call_statuses_normalize.sql колонки calls.status
-- больше нет. Эквивалент отката КЦ-классификации тогда:
--
-- BEGIN;
-- TRUNCATE TABLE call_classifications RESTART IDENTITY;
-- UPDATE calls AS c
-- SET error_message = NULL,
--     status_id = (SELECT id FROM call_statuses WHERE code = 'TRANSCRIBED')
-- FROM call_types AS ct
-- WHERE c.call_type_id = ct.id
--   AND ct.code IN ('КЦ', 'KЦ', 'KC')
--   AND c.status_id IN (
--     SELECT id FROM call_statuses WHERE code IN ('CLASSIFIED', 'CLASSIFICATION_FAILED', 'CLASSIFYING')
--   );
-- COMMIT;
--
-- ---------------------------------------------------------------------------
-- OPTIONAL: delete classification pipeline run rows (UI /pipeline-runs history).
-- Safe after TRUNCATE call_classifications (FK pipeline_run_id on classifications is gone).
-- Uncomment if you want a clean log for КЦ_CLASSIFICATION only.
--
-- BEGIN;
-- DELETE FROM pipeline_runs WHERE pipeline_code = 'КЦ_CLASSIFICATION';
-- COMMIT;
