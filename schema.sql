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
    strain_reported TEXT,           -- raw string exactly as written in paper
    strain_normalized TEXT,         -- mapped to Chemotype I/II/III
    duration_days REAL,             -- numeric duration in days (or null)
    inhaled_exposure_duration TEXT, -- inhaled exposure duration (e.g., 10 min)
    administration_frequency TEXT,  -- administration frequency (e.g., once daily)
    treatment_duration TEXT,        -- in vitro treatment duration (e.g., 24 hours)
    population TEXT,                -- human, mouse, rat, cell_line, other
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

