-- Еженедельные отчёты 911: агрегаты, текст задачи Work, путь к Excel.
-- Применить после согласования с ORM (db/models.py Weekly911Report).

CREATE TABLE IF NOT EXISTS weekly_911_reports (
    id SERIAL PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    status VARCHAR(30) NOT NULL DEFAULT 'RUNNING',
    calls_in_period INTEGER NOT NULL DEFAULT 0,
    calls_summarized_in_period INTEGER NOT NULL DEFAULT 0,
    outcome_helped INTEGER NOT NULL DEFAULT 0,
    outcome_not_helped INTEGER NOT NULL DEFAULT 0,
    outcome_in_progress INTEGER NOT NULL DEFAULT 0,
    outcome_unknown INTEGER NOT NULL DEFAULT 0,
    task_text TEXT,
    work_task_id INTEGER,
    excel_file_path VARCHAR(2048),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS ix_weekly_911_reports_period ON weekly_911_reports (period_start, period_end);
CREATE INDEX IF NOT EXISTS ix_weekly_911_reports_pipeline_run_id ON weekly_911_reports (pipeline_run_id);
CREATE INDEX IF NOT EXISTS ix_weekly_911_reports_status ON weekly_911_reports (status);
