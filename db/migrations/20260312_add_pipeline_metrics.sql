-- safe additive migration
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS total_audio_seconds DOUBLE PRECISION;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS avg_rtf DOUBLE PRECISION;
