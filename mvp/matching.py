# =============================================================
# matching.py — 案件 × 補助金・法令 のマッチング（理由を必ず返す設計）
#   スコア = W_RULE×辞書ルール一致 ＋ W_TEXT×内容類似度 ＋ W_ELIG×適格性
#   ① 辞書ルール：案件の選択値 → 政策キーワード（なぜ関係するか）
#   ② 内容類似度：案件条件 と 補助金/条文の原文 の TF-IDF コサイン類似度
#   ③ 適格性    ：受付期間・対象地域・従業員規模・業種（使えるか）
# =============================================================
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import POLICY_DICT, LABELS, SIZE_TO_EMPLOYEES, W_RULE, W_TEXT, W_ELIG


def derive_rules(filters: dict) -> list[dict]:
    rules = []
    for (key, value), rule in POLICY_DICT.items():
        if filters.get(key) == value:
            rules.append({"key": key, "value": value, **rule})
    return rules


def search_keywords(rules: list[dict], query: str) -> tuple[list[str], list[str], list[str]]:
    sub_kw = list(dict.fromkeys(k for r in rules for k in r["subsidy_kw"]))
    law_kw = list(dict.fromkeys(k for r in rules for k in r["law_kw"]))
    law_titles = list(dict.fromkeys(t for r in rules for t in r["law_titles"]))
    if not sub_kw and len(query.strip()) >= 2:
        sub_kw = [query.strip().split()[0]]
    return sub_kw, law_kw, law_titles


def profile_text(query: str, filters: dict) -> str:
    return " ".join([query, *filters.values()]).strip()


def _text_similarity(profile: str, docs: list[dict]) -> np.ndarray:
    texts = [f"{d.get('title', '')} {d.get('body', '')}" for d in docs]
    if not texts or not profile:
        return np.zeros(len(texts))
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), sublinear_tf=True)
    m = vec.fit_transform([profile] + texts)
    cos = cosine_similarity(m[0], m[1:]).ravel()
    return cos / cos.max() if cos.max() > 0 else cos


def _rule_hit(doc: dict, rules: list[dict], kw_field: str) -> tuple[float, list[str]]:
    text = f"{doc.get('title', '')} {doc.get('body', '')}"
    best, reasons = 0.0, []
    for r in rules:
        kws = r[kw_field]
        title_hit = any(t == doc.get("title") for t in r.get("law_titles", [])) if kw_field == "law_kw" else False
        in_title = any(k in (doc.get("title") or "") for k in kws)
        in_body = any(k in text for k in kws) or doc.get("query_kw") in kws
        if title_hit or in_title or in_body:
            best = max(best, 1.0 if (title_hit or in_title) else 0.7)
            hit_words = [k for k in kws if k in text] or [doc.get("query_kw") or doc.get("title")]
            reasons.append(f"{LABELS[r['key']]}「{r['value']}」→ {r['reason']}（一致語：{', '.join(hit_words)}）")
    return best, reasons


def _parse_dt(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _employee_ok(target: str, size: str | None):
    if not size:
        return None, "顧客規模が未選択のため未判定"
    if not target or "制約なし" in target:
        return True, "従業員数の制約なし"
    nums = [int(n) for n in re.findall(r"\d+", target)]
    if not nums:
        return None, f"要件「{target}」を要確認"
    return SIZE_TO_EMPLOYEES[size] <= max(nums), f"要件「{target}」／顧客「{size}」"


def check_eligibility(meta: dict, filters: dict, now: datetime):
    checks = []
    end = _parse_dt(meta.get("end", ""))
    if end:
        days = (end - now).days
        if days < 0:
            checks.append(("受付期間", False, "募集終了"))
        else:
            checks.append(("受付期間", True if days > 14 else None,
                           f"締切まで残り{days}日" + ("（締切間近）" if days <= 14 else "")))
    else:
        checks.append(("受付期間", None, "締切日不明"))

    area, pref = meta.get("target_area") or "", filters.get("prefecture")
    if not pref:
        checks.append(("対象地域", None, f"対象：{area or '不明'}（所在地未選択）"))
    else:
        checks.append(("対象地域", ("全国" in area) or (pref in area) or not area, f"対象：{area or '不明'}／顧客：{pref}"))

    ok, msg = _employee_ok(meta.get("target_employees", ""), filters.get("size"))
    checks.append(("従業員規模", ok, msg))

    ind = meta.get("industry") or ""
    ok = ("製造" in ind) or ("全業種" in ind) or ("制約なし" in ind)
    checks.append(("対象業種", ok if ind else None, ind or "業種情報なし（詳細未取得）"))
    return checks


def _elig_score(checks) -> tuple[float, bool]:
    vals = [1.0 if ok is True else 0.0 if ok is False else 0.5 for _, ok, _ in checks]
    hard_ng = any(ok is False and name in ("受付期間", "対象地域", "従業員規模") for name, ok, _ in checks)
    return float(np.mean(vals)) if vals else 0.5, hard_ng


def _is_demo(d: dict) -> bool:
    return str(d.get("ext_id", "")).startswith("DEMO")


def match_subsidies(query: str, filters: dict, docs: list[dict], lost_factors: list[str] | None = None,
                    now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    rules = derive_rules(filters)
    sims = _text_similarity(profile_text(query, filters), docs)
    rows = []
    for d, sim in zip(docs, sims):
        rule_s, reasons = _rule_hit(d, rules, "subsidy_kw")
        checks = check_eligibility(d.get("meta", {}), filters, now)
        elig_s, hard_ng = _elig_score(checks)
        score = W_RULE * rule_s + W_TEXT * sim + W_ELIG * elig_s
        if hard_ng:
            score *= 0.3
        if sim > 0.3:
            reasons.append(f"案件内容と補助金本文が近い（相対類似度 {sim:.2f}）")
        if (rule_s > 0 or sim > 0.3) and lost_factors and {"価格", "補助金・資金計画"} & set(lost_factors):
            reasons.append("類似の失注案件で『価格／資金計画』が敗因 → 補助金で実質負担を下げる提案が有効")
        m = d.get("meta", {})
        rows.append({
            "title": d["title"], "url": d.get("url"), "score": round(score, 3),
            "rule": round(rule_s, 2), "text_sim": round(float(sim), 2), "eligibility": round(elig_s, 2),
            "hard_ng": hard_ng, "reasons": reasons or ["辞書ルール該当なし（本文類似のみ）"], "checks": checks,
            "max_limit": m.get("max_limit"), "subsidy_rate": m.get("subsidy_rate"), "end": m.get("end"),
            "query_kw": d.get("query_kw"), "is_demo": _is_demo(d), "fetched_at": d.get("fetched_at"),
        })
    df = pd.DataFrame(rows)
    return df.sort_values("score", ascending=False) if not df.empty else df


def match_laws(query: str, filters: dict, docs: list[dict]) -> pd.DataFrame:
    rules = derive_rules(filters)
    sims = _text_similarity(profile_text(query, filters), docs)
    rows = []
    for d, sim in zip(docs, sims):
        rule_s, reasons = _rule_hit(d, rules, "law_kw")
        roles = [r["law_role"] for r in rules
                 if d.get("title") in r["law_titles"] or d.get("query_kw") in r["law_kw"]]
        score = 0.6 * rule_s + 0.4 * sim
        if (d.get("meta", {}).get("repeal_status") or "None") != "None":
            score, reasons = 0.0, reasons + ["廃止・失効済みの法令"]
        rows.append({
            "title": d["title"], "url": d.get("url"), "score": round(score, 3), "rule": round(rule_s, 2),
            "text_sim": round(float(sim), 2), "role": roles[0] if roles else "参考",
            "reasons": reasons or ["辞書ルール該当なし（本文類似のみ）"], "excerpt": (d.get("body") or "")[:200],
            "is_demo": _is_demo(d), "fetched_at": d.get("fetched_at"),
        })
    df = pd.DataFrame(rows)
    return df[df["score"] > 0].sort_values("score", ascending=False) if not df.empty else df
