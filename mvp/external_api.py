# =============================================================
# external_api.py — 外部情報の取得（Jグランツ API / e-Gov 法令API v2）
#   ・認証不要の公開API。取得結果は ext_docs に原文・出典URL・取得日時つきで保存
#   ・Jグランツは呼び出し上限 10回/秒、短時間の大量アクセスは規約で禁止 → 間隔を空ける
# =============================================================
import html
import re
import time

import requests

from database import upsert_ext_doc

TIMEOUT = 15
HEADERS = {"User-Agent": "TechO-Search-MVP/0.2"}
WAIT = 0.2   # API呼び出しの間隔（秒）

JGRANTS_BASE = "https://api.jgrants-portal.go.jp/exp/v1/public"
JGRANTS_FRONT = "https://www.jgrants-portal.go.jp/subsidy/"
EGOV_BASE = "https://laws.e-gov.go.jp/api/2"
EGOV_FRONT = "https://laws.e-gov.go.jp/law/"


def _strip_html(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ── Jグランツ ─────────────────────────────────────────────────
def fetch_subsidies(keyword: str, with_detail: int = 5) -> list[dict]:
    """keyword/sort/order/acceptance の4つが必須、keyword は2文字以上"""
    if len(keyword.strip()) < 2:
        return []
    params = {"keyword": keyword, "sort": "acceptance_end_datetime", "order": "ASC", "acceptance": "1"}
    r = requests.get(f"{JGRANTS_BASE}/subsidies", params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("result", []) or []

    docs = []
    for i, it in enumerate(items[:20]):
        detail = {}
        if i < with_detail:
            try:
                time.sleep(WAIT)
                d = requests.get(f"{JGRANTS_BASE}/subsidies/id/{it['id']}", headers=HEADERS, timeout=TIMEOUT)
                res = d.json().get("result", []) or []         # 存在しないIDでも200＋空配列
                detail = res[0] if res else {}
            except Exception:
                detail = {}
        body = " ".join([detail.get("subsidy_catch_phrase") or "", _strip_html(detail.get("detail")),
                         detail.get("use_purpose") or it.get("use_purpose") or ""])
        doc = {
            "source": "jgrants", "ext_id": it["id"],
            "title": it.get("title") or "",                   # name は内部コード
            "body": body.strip(),
            "url": detail.get("front_subsidy_detail_page_url") or f"{JGRANTS_FRONT}{it['id']}",
            "query_kw": keyword,
            "meta": {
                "target_area": it.get("target_area_search") or "",
                "target_employees": it.get("target_number_of_employees") or "",
                "industry": detail.get("industry") or "",
                "max_limit": it.get("subsidy_max_limit"),
                "subsidy_rate": detail.get("subsidy_rate") or "",
                "start": it.get("acceptance_start_datetime") or "",
                "end": it.get("acceptance_end_datetime") or "",
            },
        }
        upsert_ext_doc(doc)
        docs.append(doc)
    return docs


# ── e-Gov 法令API v2 ──────────────────────────────────────────
def _law_doc(law_info: dict, rev: dict, body: str, kw: str) -> dict:
    law_id = law_info.get("law_id") or ""
    return {
        "source": "egov", "ext_id": law_id,
        "title": rev.get("law_title") or "",
        "body": body,
        "url": f"{EGOV_FRONT}{law_id}",
        "query_kw": kw,
        "meta": {"law_num": law_info.get("law_num") or "", "law_type": law_info.get("law_type") or "",
                 "category": rev.get("category") or "",
                 "repeal_status": rev.get("repeal_status") or "None"},
    }


def fetch_laws_by_title(title: str, limit: int = 3) -> list[dict]:
    time.sleep(WAIT)
    params = {"law_title": title, "repeal_status": "None", "limit": limit}
    r = requests.get(f"{EGOV_BASE}/laws", params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    docs = []
    for it in r.json().get("laws", []) or []:
        rev = it.get("current_revision_info") or it.get("revision_info") or {}
        doc = _law_doc(it.get("law_info", {}), rev, f"法令名一致：{title}", title)
        upsert_ext_doc(doc)
        docs.append(doc)
    return docs


def fetch_laws_by_keyword(keyword: str, limit: int = 5) -> list[dict]:
    time.sleep(WAIT)
    params = {"keyword": keyword, "law_type": "Act,CabinetOrder,MinisterialOrdinance", "limit": limit}
    r = requests.get(f"{EGOV_BASE}/keyword", params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    docs = []
    for it in r.json().get("items", []) or []:
        sentences = [_strip_html(s.get("text")) for s in (it.get("sentences") or [])[:3]]
        doc = _law_doc(it.get("law_info", {}), it.get("revision_info", {}), " / ".join(sentences), keyword)
        upsert_ext_doc(doc)
        docs.append(doc)
    return docs


# ── DEMO データ（API に接続できない環境での画面確認用）─────────────
# ※補助金は架空。法令名は実在するが本文は要旨（デモ用に作成）。
#   APIから取得した情報ではないため、出典表示ではなく「デモ（架空）」と表示する
DEMO_SUBSIDIES = [
    {"source": "jgrants", "ext_id": "DEMO-001", "title": "【デモ】中小企業省力化投資補助事業（仮）",
     "body": "人手不足に悩む中小企業が省力化・自動化設備（ロボット、自動搬送等）を導入する経費の一部を補助",
     "url": "", "query_kw": "省力化",
     "meta": {"target_area": "全国", "target_employees": "従業員数の制約なし", "industry": "製造業 / 建設業",
              "max_limit": 15000000, "subsidy_rate": "1/2以内", "start": "2026-09-01T00:00:00Z",
              "end": "2026-12-25T08:00:00Z"}},
    {"source": "jgrants", "ext_id": "DEMO-002", "title": "【デモ】ものづくり・生産性向上設備投資補助金（仮）",
     "body": "革新的な製品・サービス開発や生産プロセス改善のための設備投資を支援。付加価値額の向上計画が必要",
     "url": "", "query_kw": "ものづくり",
     "meta": {"target_area": "全国", "target_employees": "300名以下", "industry": "製造業",
              "max_limit": 12500000, "subsidy_rate": "1/2以内（小規模2/3）", "start": "2026-08-01T00:00:00Z",
              "end": "2026-11-30T08:00:00Z"}},
    {"source": "jgrants", "ext_id": "DEMO-003", "title": "【デモ】工場・事業場の省エネ設備更新支援（仮）",
     "body": "高効率設備への更新によりエネルギー消費量を削減する事業者に対し、設備費を補助。省エネ効果の算定が必要",
     "url": "", "query_kw": "省エネ",
     "meta": {"target_area": "全国", "target_employees": "従業員数の制約なし", "industry": "全業種",
              "max_limit": 100000000, "subsidy_rate": "1/3以内", "start": "2026-07-01T00:00:00Z",
              "end": "2026-10-31T08:00:00Z"}},
    {"source": "jgrants", "ext_id": "DEMO-004", "title": "【デモ】愛知県 次世代ものづくりDX推進補助金（仮）",
     "body": "県内中小製造業のIoT・デジタル化による生産性向上を支援。稼働監視システム導入等が対象",
     "url": "", "query_kw": "デジタル",
     "meta": {"target_area": "愛知県", "target_employees": "100名以下", "industry": "製造業",
              "max_limit": 5000000, "subsidy_rate": "2/3以内", "start": "2026-09-15T00:00:00Z",
              "end": "2026-11-15T08:00:00Z"}},
    {"source": "jgrants", "ext_id": "DEMO-005", "title": "【デモ】設備更新による事業継続強化補助（募集終了）",
     "body": "老朽化設備の更新による事業継続力強化を支援",
     "url": "", "query_kw": "設備更新",
     "meta": {"target_area": "全国", "target_employees": "従業員数の制約なし", "industry": "製造業",
              "max_limit": 30000000, "subsidy_rate": "1/2以内", "start": "2026-04-01T00:00:00Z",
              "end": "2026-06-30T08:00:00Z"}},
]
DEMO_LAWS = [
    {"source": "egov", "ext_id": "DEMO-L01", "title": "労働安全衛生規則",
     "body": "（要旨・デモ）産業用ロボットを用いる作業での接触防止措置（柵・囲い等）や教育に関する規定",
     "url": "", "query_kw": "産業用ロボット", "meta": {"law_type": "MinisterialOrdinance"}},
    {"source": "egov", "ext_id": "DEMO-L02", "title": "中小企業等経営強化法",
     "body": "（要旨・デモ）経営力向上計画の認定により、設備投資に係る税制・金融支援を受けられる",
     "url": "", "query_kw": "経営力向上", "meta": {"law_type": "Act"}},
    {"source": "egov", "ext_id": "DEMO-L03",
     "title": "エネルギーの使用の合理化及び非化石エネルギーへの転換等に関する法律",
     "body": "（要旨・デモ）一定規模以上のエネルギー使用事業者に、使用状況の報告と合理化の努力を求める",
     "url": "", "query_kw": "エネルギーの使用の合理化", "meta": {"law_type": "Act"}},
    {"source": "egov", "ext_id": "DEMO-L04", "title": "輸出貿易管理令",
     "body": "（要旨・デモ）一定の性能を持つ数値制御工作機械等の輸出には経済産業大臣の許可が必要",
     "url": "", "query_kw": "工作機械", "meta": {"law_type": "CabinetOrder"}},
    {"source": "egov", "ext_id": "DEMO-L05", "title": "情報処理の促進に関する法律",
     "body": "（要旨・デモ）DX推進に取り組む事業者の認定制度（DX認定）の根拠",
     "url": "", "query_kw": "情報処理", "meta": {"law_type": "Act"}},
]


def load_demo_data() -> int:
    for d in DEMO_SUBSIDIES + DEMO_LAWS:
        upsert_ext_doc(d)
    return len(DEMO_SUBSIDIES) + len(DEMO_LAWS)
