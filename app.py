# =============================================================
# app.py — TechO Search MVP（過去案件 × 外部情報 の提案作成支援）
# 起動：streamlit run app.py
# 構成：app.py / config.py / database.py / schema.sql / ranking.py
#       matching.py / external_api.py / attribution.py / seed_data.py
# 流れ：似た案件を見つける → 勝因・敗因を知る → 提案の材料を集める
# =============================================================
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import (APP_NAME, APP_VERSION, CATEGORY_FIELDS, CATEGORY_KEYS, LABELS, STATUS_OPTIONS, BRANCHES,
                    FREE_TEXT_TEMPLATE, FACTOR_HINTS, W_RULE, W_TEXT, W_ELIG, W_COS, W_META, W_FTS)
from database import (init_db, insert_case, delete_case, get_cases_df, fts_hit_ids, get_ext_docs,
                      log_search, get_search_logs_df)
from ranking import CaseSearchEngine
from matching import derive_rules, search_keywords, match_subsidies, match_laws
from external_api import fetch_subsidies, fetch_laws_by_keyword, fetch_laws_by_title, load_demo_data
from attribution import item_source, section_source, sidebar_sources, footer_sources
from seed_data import seed

NONE = "（指定なし）"
FIELD = {f["key"]: f for f in CATEGORY_FIELDS}

init_db()
seed()
st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide")


@st.cache_resource
def get_engine(cache_key: tuple) -> CaseSearchEngine:
    """cache_key（件数, 最大ID）が変わった時だけ TF-IDF を再構築"""
    return CaseSearchEngine().fit(get_cases_df())


cases_df = get_cases_df()
engine = get_engine((len(cases_df), int(cases_df["id"].max()) if not cases_df.empty else 0))

for k, v in {"query": "", "filters": {}, "results": None, "lost_factors": []}.items():
    st.session_state.setdefault(k, v)

# ── ヘッダー・サイドバー ─────────────────────────────────────────
st.title(f"🔍 {APP_NAME}")
st.caption(f"{APP_VERSION} ｜ 似た案件を見つける → 勝因・敗因を知る → 提案の材料を集める")

with st.sidebar:
    st.header("DB の状態")
    c1, c2 = st.columns(2)
    c1.metric("過去案件", f"{len(cases_df)} 件")
    c2.metric("外部情報", f"{len(get_ext_docs())} 件")
    if not cases_df.empty:
        st.metric("登録案件の受注率", f"{(cases_df['status'] == '受注').mean():.0%}")
    with st.expander("スコアの仕組み"):
        st.markdown(
            f"**類似案件**\n\n`{W_COS}×コサイン類似度 + {W_META}×項目一致率 + {W_FTS}×全文一致`\n\n"
            f"**提案材料（補助金）**\n\n`{W_RULE}×辞書ルール + {W_TEXT}×内容類似 + {W_ELIG}×適格性`\n\n"
            "受付終了・地域外・規模外は ×0.3 で下位に表示")
    sidebar_sources()

tab_search, tab_material, tab_register, tab_dash, tab_list = st.tabs(
    ["🔍 類似案件検索（受注 vs 失注）", "📎 提案材料（補助金・法令）", "📝 過去案件登録", "📊 ダッシュボード", "📋 一覧・取込"])


def case_card(row: pd.Series, color: str):
    with st.container(border=True):
        st.markdown(f"**{row['title']}**")
        st.caption(f"{row['case_date']} ｜ {row['branch']} ｜ スコア {row['score']:.2f}"
                   f"（類似度 {row['cosine']:.2f}・項目一致 {row['meta_match']:.0%}{'・全文一致' if row['fts_hit'] else ''}）")
        st.markdown(f":{color}-badge[{row['status']}要因：{row['factor']}]")
        if row["matched_items"]:
            st.caption("一致した条件：" + "、".join(row["matched_items"]))
        st.caption(" / ".join(str(row[k]) for k in ["industry", "machine", "issue", "competitor"]))
        text = row["free_text"] or ""
        reason = next((l for l in text.splitlines() if l.startswith("【決め手")), "")
        if reason:
            st.markdown(f"> {reason}")
        with st.expander("原文を見る"):
            st.text(text)
            if row.get("source_url"):
                st.markdown(f"🔗 [原本（日報・提案書）]({row['source_url']})")


def filter_widgets(prefix: str, defaults: dict) -> dict:
    keys = ["industry", "machine", "issue", "size", "prefecture", "competitor"]
    cols = st.columns(3)
    out = {}
    for i, k in enumerate(keys):
        opts = [NONE] + FIELD[k]["options"]
        idx = opts.index(defaults[k]) if defaults.get(k) in opts else 0
        v = cols[i % 3].selectbox(LABELS[k], opts, index=idx, key=f"{prefix}_{k}")
        if v != NONE:
            out[k] = v
    return out


# ── 1. 類似案件検索 ─────────────────────────────────────────────
with tab_search:
    st.subheader("新しい案件の条件から、似た過去案件を『受注』『失注』で並べて比べる")
    with st.form("search_form"):
        q = st.text_input("案件の特徴（自由入力）", value=st.session_state.query,
                          placeholder="例：夜間無人運転したい 段取り要員不足 横形MC")
        st.caption("条件を選ぶほど、項目一致スコアと補助金・法令のマッチング精度が上がります")
        f = filter_widgets("s", st.session_state.filters)
        top_n = st.slider("受注・失注それぞれの表示件数", 1, 10, 3)
        submitted = st.form_submit_button("🔍 検索", type="primary")

    if submitted:
        if not q.strip() and not f:
            st.warning("自由入力か条件のどちらかを入れてください")
        else:
            res = engine.search(q, f, fts_hit_ids(q), top_n=top_n)
            st.session_state.update(query=q, filters=f, results=res,
                                    lost_factors=res[res["status"] == "失注"]["factor"].tolist() if not res.empty else [])
            log_search(q, f, int((res["status"] == "受注").sum()) if not res.empty else 0,
                       int((res["status"] == "失注").sum()) if not res.empty else 0)

    res = st.session_state.results
    if res is not None:
        if res.empty:
            st.info("該当する案件が見つかりませんでした。条件を減らすか、言い換えてください。")
        else:
            won, lost = res[res["status"] == "受注"], res[res["status"] == "失注"]
            col_w, col_l = st.columns(2)
            with col_w:
                st.markdown(f"### 🟢 受注した似た案件（{len(won)}件）")
                for _, r in won.iterrows():
                    case_card(r, "green")
                if won.empty:
                    st.info("受注事例なし")
            with col_l:
                st.markdown(f"### 🔴 失注した似た案件（{len(lost)}件）")
                for _, r in lost.iterrows():
                    case_card(r, "red")
                if lost.empty:
                    st.info("失注事例なし")

            st.divider()
            st.markdown("### 🔎 受注と失注は、どこが違ったか")
            if not won.empty and not lost.empty:
                rows = []
                for k in CATEGORY_KEYS:
                    w = won[k].mode().iat[0] if not won[k].mode().empty else "-"
                    l = lost[k].mode().iat[0] if not lost[k].mode().empty else "-"
                    rows.append({"項目": LABELS[k], "受注側で多い": w, "失注側で多い": l,
                                 "差": "⚠️ 違う" if w != l else "同じ"})
                cmp = pd.DataFrame(rows)
                st.dataframe(cmp.style.apply(
                    lambda r: ["background-color:#fff4e5" if r["差"] != "同じ" else "" for _ in r], axis=1),
                    hide_index=True, width="stretch")

            c1, c2 = st.columns(2)
            with c1:
                fc = res.groupby(["status", "factor"]).size().reset_index(name="件数")
                fig = px.bar(fc, x="件数", y="factor", color="status", orientation="h", barmode="group",
                             color_discrete_map={"受注": "#2e7d32", "失注": "#c62828"},
                             labels={"factor": "勝因・敗因"}, title="勝因・敗因の比較")
                fig.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig, width="stretch")
            with c2:
                st.markdown("**💡 次の提案で考えるべき問い**")
                for fac in dict.fromkeys(lost["factor"]):
                    st.markdown(f"- 失注要因『{fac}』：{FACTOR_HINTS.get(fac, '')}")
                for fac in dict.fromkeys(won["factor"]):
                    st.markdown(f"- 受注要因『{fac}』：今回の提案にも組み込めるか？")
                st.info("次は『📎 提案材料』タブで、この条件に合う補助金・法令を確認できます")


# ── 2. 提案材料（補助金・法令）────────────────────────────────────
with tab_material:
    st.subheader("この案件に使える補助金・確認すべき法令と、その『理由』")
    q, f = st.session_state.query, st.session_state.filters
    if not q and not f:
        st.info("先に『🔍 類似案件検索』で案件の条件を入れてください（条件がマッチングの入力になります）")
    else:
        st.markdown("**今回の案件条件：** " + (q or "（自由入力なし）") + " ／ " +
                    "、".join(f"{LABELS[k]}={v}" for k, v in f.items()))

        rules = derive_rules(f)
        sub_kw, law_kw, law_titles = search_keywords(rules, q)
        with st.expander(f"① 適用された辞書ルール（{len(rules)}件）― なぜこの検索語になったか", expanded=True):
            if rules:
                st.dataframe(pd.DataFrame([{
                    "案件の条件": f"{LABELS[r['key']]}：{r['value']}", "補助金の検索語": "、".join(r["subsidy_kw"]),
                    "法令の検索語・法令名": "、".join(r["law_kw"] + r["law_titles"]), "関連する理由": r["reason"]}
                    for r in rules]), hide_index=True, width="stretch")
            else:
                st.warning("辞書ルールに該当なし。③顧客課題・②提案機種を選ぶと精度が上がります（自由入力語で検索します）")

        c1, c2 = st.columns([2, 1])
        mode = c1.radio("取得方法", ["API（Jグランツ・法令API v2）", "デモデータ（API接続できない環境向け）"], horizontal=True)
        if c2.button("📥 外部情報を取得", type="primary"):
            if mode.startswith("API"):
                errs = []
                with st.spinner("Jグランツ・法令APIから取得中..."):
                    for fn, kws, name in [(fetch_subsidies, sub_kw, "Jグランツ"),
                                          (fetch_laws_by_keyword, law_kw, "法令キーワード"),
                                          (fetch_laws_by_title, law_titles, "法令名")]:
                        for kw in kws:
                            try:
                                fn(kw)
                            except Exception as e:
                                errs.append(f"{name}『{kw}』：{e}")
                if errs:
                    st.error("一部取得に失敗しました（社内ネットワーク制限の可能性）。デモデータで画面確認できます。\n\n"
                             + "\n".join(f"- {e}" for e in errs))
                else:
                    st.success("取得しました（取得結果と取得日は DB に保存され、出典表示に使われます）")
            else:
                st.success(f"デモデータ {load_demo_data()} 件を読み込みました")

        # ── 補助金 ──
        sub_docs = get_ext_docs("jgrants")
        if sub_docs:
            sdf = match_subsidies(q, f, sub_docs, st.session_state.lost_factors).head(8)
            st.markdown("### 💴 補助金（Jグランツ）")
            fig = go.Figure()
            for col, name, w, colr in [("rule", "辞書ルール", W_RULE, "#1f3864"), ("text_sim", "内容類似", W_TEXT, "#5b9bd5"),
                                       ("eligibility", "適格性", W_ELIG, "#a9d18e")]:
                fig.add_bar(y=sdf["title"].str[:28], x=sdf[col] * w, name=name, orientation="h", marker_color=colr)
            fig.update_layout(barmode="stack", height=60 + 40 * len(sdf), title="スコアの内訳（なぜ上位か）",
                              yaxis=dict(autorange="reversed"), margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, width="stretch")
            st.caption("※スコア・内訳グラフは本アプリが算出したもので、Jグランツの情報ではありません")

            for _, r in sdf.iterrows():
                with st.container(border=True):
                    head = "❌ 対象外の可能性 ｜ " if r["hard_ng"] else ""
                    st.markdown(f"**{head}{r['title']}**　スコア {r['score']:.2f}")
                    m1, m2, m3 = st.columns(3)
                    m1.caption(f"上限：{int(r['max_limit']):,}円" if pd.notna(r["max_limit"]) and r["max_limit"] else "上限：-")
                    m2.caption(f"補助率：{r['subsidy_rate'] or '-'}")
                    m3.caption(f"締切：{(r['end'] or '-')[:10]}")
                    st.markdown("**関連する理由**（本アプリの判定）")
                    for reason in r["reasons"]:
                        st.markdown(f"- {reason}")
                    st.markdown("**使えるか（適格性チェック・本アプリの判定）**　" + "　".join(
                        f"{'✅' if ok is True else '❌' if ok is False else '⚠️'}{name}：{msg}" for name, ok, msg in r["checks"]))
                    item_source("jgrants", r["url"], r["fetched_at"], r["is_demo"])
            section_source("jgrants", sdf)

        # ── 法令 ──
        law_docs = get_ext_docs("egov")
        if law_docs:
            ldf = match_laws(q, f, law_docs).head(6)
            st.markdown("### ⚖️ 法令（e-Gov 法令API v2）")
            for _, r in ldf.iterrows():
                with st.container(border=True):
                    badge = ":green-badge[追い風（提案材料）]" if r["role"] == "追い風" else \
                        ":orange-badge[要確認（リスク説明）]" if r["role"] == "要確認" else ":gray-badge[参考]"
                    st.markdown(f"{badge} **{r['title']}**　スコア {r['score']:.2f}")
                    for reason in r["reasons"]:
                        st.markdown(f"- {reason}")
                    if r["excerpt"]:
                        st.caption(f"条文の抜粋：{r['excerpt']}")
                    item_source("egov", r["url"], r["fetched_at"], r["is_demo"])
            section_source("egov", ldf)

        if not sub_docs and not law_docs:
            st.info("『📥 外部情報を取得』を押してください")
        st.caption("※マッチングは候補提示です。補助金の要件・法令の解釈は必ず原文（公募要領・条文）で確認してください")


# ── 3. 過去案件登録 ─────────────────────────────────────────────
with tab_register:
    st.subheader("過去案件を登録（8項目を選ぶ → 自由記述 → 受注／失注）")
    if st.session_state.get("registered"):
        st.success(f"「{st.session_state.pop('registered')}」を登録しました（検索インデックスに自動反映済み）")

    with st.form("register_form", clear_on_submit=True):
        st.markdown("**必須項目**")
        a1, a2, a3, a4 = st.columns([3, 1, 1, 1])
        title = a1.text_input("案件名 *", placeholder="例：A精工 横形MC2台更新")
        status = a2.radio("ステータス *", STATUS_OPTIONS, horizontal=True)
        case_date = a3.date_input("日付 *", value=date.today())
        branch = a4.selectbox("拠点 *", BRANCHES)

        st.markdown("**大項目（8項目・選択式）**")
        vals, cols = {}, st.columns(4)
        for i, fdef in enumerate(CATEGORY_FIELDS):
            label = fdef["label"] + (" *" if fdef["key"] == "factor" else "")
            vals[fdef["key"]] = cols[i % 4].selectbox(label, fdef["options"], key=f"r_{fdef['key']}")

        st.markdown("**自由記述（最小限でOK：4つの観点だけ埋める）**")
        free_text = st.text_area("自由記述", value=FREE_TEXT_TEMPLATE, height=160, label_visibility="collapsed")
        b1, b2 = st.columns(2)
        source_url = b1.text_input("原本リンク（日報・提案書の保存場所）", placeholder="https://...")
        owner = b2.text_input("担当者")
        ok = st.form_submit_button("💾 登録", type="primary")

    if ok:
        if not title.strip():
            st.error("案件名は必須です")
        else:
            insert_case({"title": title.strip(), "status": status, "case_date": case_date.isoformat(),
                         "branch": branch, **vals, "free_text": free_text, "source_url": source_url, "owner": owner})
            st.session_state["registered"] = title.strip()
            st.rerun()


# ── 4. ダッシュボード ───────────────────────────────────────────
with tab_dash:
    st.subheader("蓄積データの見える化")
    if cases_df.empty:
        st.info("案件がありません")
    else:
        d1, d2 = st.columns(2)
        wr = cases_df.groupby("issue")["status"].apply(lambda s: (s == "受注").mean()).reset_index(name="受注率")
        fig = px.bar(wr.sort_values("受注率"), x="受注率", y="issue", orientation="h", title="顧客課題別の受注率",
                     labels={"issue": ""}, color_discrete_sequence=["#1f3864"])
        fig.update_xaxes(tickformat=".0%")
        d1.plotly_chart(fig, width="stretch")
        lost = cases_df[cases_df["status"] == "失注"]["factor"].value_counts().reset_index()
        lost.columns = ["敗因", "件数"]
        d2.plotly_chart(px.pie(lost, names="敗因", values="件数", title="失注要因の内訳", hole=0.4), width="stretch")
        br = cases_df.groupby(["branch", "status"]).size().reset_index(name="件数")
        st.plotly_chart(px.bar(br, x="branch", y="件数", color="status", title="拠点別の登録件数",
                               color_discrete_map={"受注": "#2e7d32", "失注": "#c62828"}, labels={"branch": ""}),
                        width="stretch")
        logs = get_search_logs_df()
        st.markdown("**検索ログ（効果測定用）**")
        if logs.empty:
            st.caption("まだ検索ログがありません")
        else:
            l1, l2 = st.columns(2)
            l1.metric("検索回数", f"{len(logs)} 回")
            l2.metric("ゼロ件率（低いほど良い）", f"{((logs['hit_won'] + logs['hit_lost']) == 0).mean():.0%}")


# ── 5. 一覧・CSV取込 ───────────────────────────────────────────
with tab_list:
    st.subheader(f"登録済み案件（{len(cases_df)} 件）")
    if not cases_df.empty:
        show = cases_df[["id", "case_date", "title", "status", "branch", *CATEGORY_KEYS]].rename(
            columns={"case_date": "日付", "title": "案件名", "status": "結果", "branch": "拠点", **LABELS})
        st.dataframe(show, hide_index=True, width="stretch")
        dc1, dc2 = st.columns([1, 3])
        del_id = dc1.number_input("削除する案件ID", min_value=0, step=1)
        if dc2.button("🗑️ 削除") and del_id:
            delete_case(int(del_id))
            st.rerun()

    st.divider()
    st.markdown("**既存の失注記録・日報（CSV）を一括取込**")
    cols_required = ["title", "status", "case_date", "branch", *CATEGORY_KEYS, "free_text", "source_url", "owner"]
    st.download_button("📄 取込用テンプレートCSV", pd.DataFrame(columns=cols_required).to_csv(index=False).encode("utf-8-sig"),
                       "techo_import_template.csv", "text/csv")
    up = st.file_uploader("CSV を選択", type="csv")
    if up is not None:
        imp = pd.read_csv(up).fillna("")
        missing = [c for c in ["title", "status", "case_date", "branch", "factor"] if c not in imp.columns]
        if missing:
            st.error(f"必須列がありません：{missing}")
        else:
            bad = imp[~imp["status"].isin(STATUS_OPTIONS)]
            st.dataframe(imp.head(), width="stretch")
            if len(bad):
                st.warning(f"status が『受注／失注』以外の {len(bad)} 行は取り込みません")
            if st.button("📥 取り込む"):
                for _, row in imp[imp["status"].isin(STATUS_OPTIONS)].iterrows():
                    insert_case(row.to_dict())
                st.success("取り込みました")
                st.rerun()

st.divider()
st.caption(f"© PROJECT ZERO — {APP_NAME} {APP_VERSION} ｜ SQLite + FTS5 + TF-IDF/コサイン類似度")
st.caption("外部データ：" + footer_sources())
