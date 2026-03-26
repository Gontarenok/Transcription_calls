-- additive migration for editable topic catalog and call classification results
-- IMPORTANT: this migration intentionally does NOT repeat changes from
-- 20260305_add_pipeline_code_to_pipeline_runs.sql and
-- 20260306_add_active_and_pipeline_metrics.sql.
-- Apply those earlier migrations separately if they were not executed yet.

CREATE TABLE IF NOT EXISTS topic_catalog_entries (
    id SERIAL PRIMARY KEY,
    topic_name VARCHAR(255) NOT NULL,
    subtopic_name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    keywords_text TEXT NOT NULL,
    synonyms_text TEXT,
    negative_keywords_text TEXT,
    doc_text TEXT NOT NULL,
    source_name VARCHAR(100) DEFAULT 'reference_topics.txt',
    source_hash VARCHAR(64),
    qdrant_point_id VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_topic_catalog_topic_subtopic UNIQUE (topic_name, subtopic_name),
    CONSTRAINT uq_topic_catalog_qdrant_point_id UNIQUE (qdrant_point_id)
);

CREATE INDEX IF NOT EXISTS ix_topic_catalog_is_active ON topic_catalog_entries (is_active);

CREATE TABLE IF NOT EXISTS call_classifications (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    transcription_id INTEGER REFERENCES transcriptions(id) ON DELETE SET NULL,
    catalog_entry_id INTEGER REFERENCES topic_catalog_entries(id) ON DELETE SET NULL,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    model_name VARCHAR(100) NOT NULL,
    embedding_model_name VARCHAR(255),
    prompt_version VARCHAR(50),
    classifier_version VARCHAR(50),
    spravochnik_version VARCHAR(50),
    decision_mode VARCHAR(50),
    topic_name VARCHAR(255) NOT NULL,
    subtopic_name VARCHAR(255) NOT NULL,
    confidence DOUBLE PRECISION,
    lexical_score DOUBLE PRECISION,
    semantic_score DOUBLE PRECISION,
    rerank_score DOUBLE PRECISION,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    reasoning TEXT,
    evidence_json TEXT,
    candidates_json TEXT,
    raw_llm_output TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_call_classifications_call_active ON call_classifications (call_id, is_active);
CREATE INDEX IF NOT EXISTS ix_call_classifications_subtopic ON call_classifications (subtopic_name);
CREATE INDEX IF NOT EXISTS ix_call_classifications_transcription_id ON call_classifications (transcription_id);
CREATE INDEX IF NOT EXISTS ix_call_classifications_catalog_entry_id ON call_classifications (catalog_entry_id);
CREATE INDEX IF NOT EXISTS ix_call_classifications_pipeline_run_id ON call_classifications (pipeline_run_id);