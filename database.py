# =============================================================
# database.py — SQLite アクセス層（cases / cases_fts / ext_docs / search_logs）
# =============================================================
import json
import sqlite3
from pathlib import Path

import pandas as pd

from config import DB_PATH, CATEGORY_KEYS

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
CASE_COLUMNS = ["title", "status", "case_date", "branch", "factor", *[k for k in CATEGORY_KEYS if k != "factor"],
                "free_text", "source_url", "owner"]


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def _tags(case: dict) -> str:
    return " ".join(str(case.get(k) or "") for k in CATEGORY_KEYS)


def insert_case(case: dict, db_path: str = DB_PATH) -> int:
    values = [case.get(c) for c in CASE_COLUMNS]
    with get_conn(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO cases ({','.join(CASE_COLUMNS)}) VALUES ({','.join('?' * len(CASE_COLUMNS))})", values)
        case_id = cur.lastrowid
        conn.execute("INSERT INTO cases_fts (rowid, title, free_text, tags) VALUES (?,?,?,?)",
                     (case_id, case.get("title", ""), case.get("free_text", ""), _tags(case)))
    return case_id


def delete_case(case_id: int, db_path: str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
        conn.execute("DELETE FROM cases_fts WHERE rowid=?", (case_id,))


def get_cases_df(db_path: str = DB_PATH) -> pd.DataFrame:
    with get_conn(db_path) as conn:
        return pd.read_sql_query("SELECT * FROM cases ORDER BY case_date DESC", conn)


def count_cases(db_path: str = DB_PATH) -> int:
    with get_conn(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def fts_hit_ids(query: str, db_path: str = DB_PATH) -> set[int]:
    """FTS5 trigram：3文字以上の語を含む案件ID（3文字未満は TF-IDF 側に任せる）"""
    terms = [t for t in query.replace("　", " ").split() if len(t) >= 3]
    if not terms:
        return set()
    match = " OR ".join('"' + t.replace('"', '') + '"' for t in terms)
    try:
        with get_conn(db_path) as conn:
            rows = conn.execute("SELECT rowid FROM cases_fts WHERE cases_fts MATCH ?", (match,)).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def upsert_ext_doc(doc: dict, db_path: str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO ext_docs (source, ext_id, title, body, url, meta_json, query_kw)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(source, ext_id) DO UPDATE SET
                 title=excluded.title, body=excluded.body, url=excluded.url,
                 meta_json=excluded.meta_json, query_kw=excluded.query_kw,
                 fetched_at=datetime('now','localtime')""",
            (doc["source"], doc["ext_id"], doc.get("title"), doc.get("body"), doc.get("url"),
             json.dumps(doc.get("meta", {}), ensure_ascii=False), doc.get("query_kw")))


def get_ext_docs(source: str | None = None, db_path: str = DB_PATH) -> list[dict]:
    sql, args = "SELECT * FROM ext_docs", ()
    if source:
        sql, args = sql + " WHERE source=?", (source,)
    with get_conn(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json") or "{}")
        out.append(d)
    return out


def log_search(query: str, filters: dict, hit_won: int, hit_lost: int, db_path: str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.execute("INSERT INTO search_logs (query, filters, hit_won, hit_lost) VALUES (?,?,?,?)",
                     (query, json.dumps(filters, ensure_ascii=False), hit_won, hit_lost))


def get_search_logs_df(db_path: str = DB_PATH) -> pd.DataFrame:
    with get_conn(db_path) as conn:
        return pd.read_sql_query("SELECT * FROM search_logs ORDER BY searched_at DESC", conn)
