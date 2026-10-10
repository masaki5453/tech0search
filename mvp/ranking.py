# =============================================================
# ranking.py — 類似案件検索エンジン
#   ベクトル化：TF-IDF（文字2〜3gram）／類似度：コサイン類似度
#   最終スコア：W_COS×コサイン ＋ W_META×項目一致率 ＋ W_FTS×全文一致
# =============================================================
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import CATEGORY_KEYS, LABELS, W_COS, W_META, W_FTS


def build_doc_text(row: dict) -> str:
    parts = [str(row.get("title") or "")]
    parts += [str(row.get(k) or "") for k in CATEGORY_KEYS]
    parts.append(str(row.get("free_text") or ""))
    return " ".join(parts)


class CaseSearchEngine:
    def __init__(self):
        self.df = pd.DataFrame()
        self.vectorizer = None
        self.matrix = None

    def fit(self, df: pd.DataFrame) -> "CaseSearchEngine":
        self.df = df.reset_index(drop=True).copy()
        if self.df.empty:
            return self
        docs = self.df.apply(lambda r: build_doc_text(r.to_dict()), axis=1).tolist()
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(docs)
        return self

    def search(self, query: str, filters: dict, fts_ids: set[int] | None = None, top_n: int = 5) -> pd.DataFrame:
        if self.df.empty or self.vectorizer is None:
            return pd.DataFrame()
        fts_ids = fts_ids or set()

        q_text = " ".join([query, *filters.values()]).strip()
        cos = cosine_similarity(self.vectorizer.transform([q_text]), self.matrix).ravel() if q_text else \
            np.zeros(len(self.df))
        cos_norm = cos / cos.max() if cos.max() > 0 else cos

        matched_lists, meta = [], []
        for _, r in self.df.iterrows():
            m = [LABELS[k] for k, v in filters.items() if r.get(k) == v]
            matched_lists.append(m)
            meta.append(len(m) / len(filters) if filters else 0.0)

        fts = self.df["id"].apply(lambda i: 1.0 if i in fts_ids else 0.0).to_numpy()

        out = self.df.copy()
        out["cosine"] = cos.round(3)
        out["meta_match"] = np.round(meta, 2)
        out["fts_hit"] = fts.astype(bool)
        out["matched_items"] = matched_lists
        out["score"] = (W_COS * cos_norm + W_META * np.array(meta) + W_FTS * fts).round(3)
        out = out[out["score"] > 0].sort_values("score", ascending=False)

        won = out[out["status"] == "受注"].head(top_n)
        lost = out[out["status"] == "失注"].head(top_n)
        return pd.concat([won, lost])
