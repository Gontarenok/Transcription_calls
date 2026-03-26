-- Добавляет тип пайплайна (KC/911) для фильтрации логов запусков
ALTER TABLE pipeline_runs
ADD COLUMN IF NOT EXISTS pipeline_code VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN';

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_pipeline_code ON pipeline_runs (pipeline_code);