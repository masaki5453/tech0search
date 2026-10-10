-- =============================================================
-- schema.sql — TechO Search MVP
-- 方針：原文（free_text）はそのまま保持し、必須メタデータ＋8大項目を付与する
-- =============================================================

CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('受注','失注')),
    case_date   TEXT NOT NULL,
    branch      TEXT NOT NULL,
    factor      TEXT NOT NULL,
    industry    TEXT, machine TEXT, issue TEXT, size TEXT,
    prefecture  TEXT, competitor TEXT, budget TEXT,
    free_text   TEXT,
    source_url  TEXT,
    owner       TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- 全文検索（FTS5 trigram：形態素解析なしで日本語の部分一致）
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    title, free_text, tags, tokenize = 'trigram'
);

-- 外部情報キャッシュ。原文＋取得日時（＝出典表示の「利用日」）を保持
CREATE TABLE IF NOT EXISTS ext_docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,                 -- 'jgrants' / 'egov'
    ext_id      TEXT NOT NULL,
    title       TEXT,
    body        TEXT,
    url         TEXT,                          -- 出典ページ（原文）のURL
    meta_json   TEXT,
    query_kw    TEXT,
    fetched_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (source, ext_id)
);

CREATE TABLE IF NOT EXISTS search_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT,
    filters     TEXT,
    hit_won     INTEGER,
    hit_lost    INTEGER,
    searched_at TEXT DEFAULT (datetime('now','localtime'))
);
