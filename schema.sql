-- schema.sql
-- SQLite database schema for Cannabis Research Papers Catalog

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pmid TEXT UNIQUE,
    doi TEXT UNIQUE,
    semantic_scholar_id TEXT UNIQUE,
    title TEXT NOT NULL,
    authors TEXT,                   -- JSON array of strings
    journal TEXT,
    year INTEGER,
    abstract TEXT,
    full_text_link TEXT,
    study_type TEXT,                -- RCT, observational, animal, in vitro, review, meta-analysis
    publication_type TEXT,          -- review, original research, case study, systematic review, meta-analysis, editorial, comment, letter to the editor, perspectives paper
    exposure_method TEXT,           -- smoked, vaporized, oral/edible, tincture, injection, forced inhalation, in vitro, unknown
    thc_pct REAL,                   -- numeric percentage (or null)
    cbd_pct REAL,                   -- numeric percentage (or null)
    dose_mg REAL,                   -- numeric dose in mg (or null)
    puff_count INTEGER,
    thc_mg_ml REAL,
    thc_mg_g REAL,
    thc_mg_kg REAL,
    cbd_mg_ml REAL,
    cbd_mg_g REAL,
    cbd_mg_kg REAL,
    thc_uM REAL,
    cbd_uM REAL,
    strain_reported TEXT,           -- raw string exactly as written in paper
    strain_normalized TEXT,         -- mapped to Chemotype I/II/III
    duration_days REAL,             -- numeric duration in days (or null)
    inhaled_exposure_duration TEXT, -- inhaled exposure duration (e.g., 10 min)
    administration_frequency TEXT,  -- administration frequency (e.g., once daily)
    treatment_duration TEXT,        -- in vitro treatment duration (e.g., 24 hours)
    sample_size INTEGER,            -- numeric sample size (or null)
    outcome_domain TEXT,            -- JSON array of strings: pain, anxiety, cognition, inflammation, addiction, oncology, neuroprotection, sleep, other
    open_access INTEGER DEFAULT 0,  -- boolean 0 (False) or 1 (True)
    citation_count INTEGER DEFAULT 0,
    date_harvested TEXT NOT NULL,
    publication_date TEXT,
    summary TEXT,
    expert_locked_fields TEXT DEFAULT '[]',
    classification_confidence REAL,
    classification_timestamp TEXT,
    classifier_version TEXT
);

-- Full-Text Search FTS5 Virtual Table
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title,
    abstract,
    authors,
    content='papers',
    content_rowid='id'
);

-- Sync Triggers to keep FTS table updated automatically with the primary papers table

CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, authors) VALUES (new.id, new.title, new.abstract, new.authors);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, authors) VALUES ('delete', old.id, old.title, old.abstract, old.authors);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, authors) VALUES ('delete', old.id, old.title, old.abstract, old.authors);
    INSERT INTO papers_fts(rowid, title, abstract, authors) VALUES (new.id, new.title, new.abstract, new.authors);
END;

-- System-level key-value metadata table
CREATE TABLE IF NOT EXISTS system_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Expert feedback audit table for tracking corrections and dynamic few-shot learning
CREATE TABLE IF NOT EXISTS feedback_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    title TEXT,
    abstract TEXT,
    timestamp TEXT,
    confidence_before_review REAL,
    classifier_version TEXT,
    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

-- Metrics logging table for LLM classification calls
CREATE TABLE IF NOT EXISTS llm_calls_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER,
    timestamp TEXT,
    model TEXT,
    input_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    output_tokens INTEGER,
    cost REAL,
    few_shot_similarity REAL,
    few_shot_count INTEGER,
    classification_confidence REAL,
    classifier_version TEXT,
    batch_id TEXT,
    bm25_retrieval_used INTEGER DEFAULT 0,
    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE SET NULL
);

-- Full-text index for upward-propagation few-shot retrieval over expert corrections
CREATE VIRTUAL TABLE IF NOT EXISTS feedback_audit_fts USING fts5(
    title,
    abstract,
    field_name,
    correction_text,
    tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS feedback_audit_ai AFTER INSERT ON feedback_audit BEGIN
    INSERT INTO feedback_audit_fts(
        rowid, title, abstract, field_name, correction_text
    ) VALUES (
        new.id,
        coalesce(new.title, ''),
        coalesce(new.abstract, ''),
        coalesce(new.field_name, ''),
        coalesce(new.field_name, '') || ' ' || coalesce(new.old_value, '') || ' -> ' || coalesce(new.new_value, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS feedback_audit_ad AFTER DELETE ON feedback_audit BEGIN
    INSERT INTO feedback_audit_fts(
        feedback_audit_fts, rowid, title, abstract, field_name, correction_text
    ) VALUES (
        'delete', old.id, old.title, old.abstract, old.field_name, ''
    );
END;

-- Optimization run log with field-group Hamming breakdown for single-pass RL
CREATE TABLE IF NOT EXISTS optimization_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    field_group_scores TEXT,
    reward REAL,
    gate_passed INTEGER DEFAULT 0,
    failed_attempts INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    patch_summary TEXT,
    rules_version_before TEXT,
    rules_version_after TEXT
);


