# =============================================================
# attribution.py — 外部APIの出典表示（利用規約対応）をまとめた部品
#   表示する場所は3段階：
#     ① 各カード   ：出典＋原文ページのURL＋取得日（＝利用日）
#     ② セクション末：出典＋「加工した旨・加工の主体」＋加工内容＋利用規約リンク
#     ③ フッター・サイドバー：アプリ全体の出典一覧
#   デモデータはAPI由来ではないため、出典ではなく「デモ（架空）」と明示する
# =============================================================
import streamlit as st

from config import ATTRIBUTION, PROCESSING_NOTE


def _date(s) -> str:
    return (str(s) if s else "")[:10] or "不明"


def item_source(source: str, url: str | None, fetched_at, is_demo: bool) -> None:
    """① 各カードの出典行"""
    if is_demo:
        st.caption("🧪 デモ（架空）データ：APIから取得した情報ではありません")
        return
    a = ATTRIBUTION[source]
    link = f"[{a['label']}]({url})" if url else f"[{a['label']}]({a['url']})"
    st.caption(f"{link}（{_date(fetched_at)} 取得）")


def section_source(source: str, rows) -> None:
    """② セクション末の出典・加工表示。rows は表示した結果（DataFrame）"""
    if rows is None or len(rows) == 0:
        return
    real = rows[~rows["is_demo"]]
    a = ATTRIBUTION[source]
    with st.container(border=True):
        if len(real) == 0:
            st.caption("🧪 表示中の情報はすべてデモ（架空）データです。API取得時は以下の出典を表示します。")
        dates = sorted(_date(d) for d in real["fetched_at"]) if len(real) else []
        period = f"（{dates[0]}〜{dates[-1]} に利用）" if dates and dates[0] != dates[-1] else \
            (f"（{dates[0]} に利用）" if dates else "")
        st.caption(f"📚 **[{a['label']}]({a['url']})** {period}")
        st.caption(a["processed"])
        st.caption(PROCESSING_NOTE)
        st.caption(f"利用条件：[{a['terms_name']}]({a['terms']})")


def sidebar_sources() -> None:
    """③ サイドバー：外部データの出典・利用規約"""
    with st.expander("📚 外部データの出典・利用規約"):
        for a in ATTRIBUTION.values():
            st.markdown(f"**[{a['label']}]({a['url']})**")
            st.caption(a["processed"])
            st.caption(f"[{a['terms_name']}]({a['terms']})")
        st.caption(PROCESSING_NOTE)


def footer_sources() -> str:
    """③ フッター用の1行"""
    return " ／ ".join(f"[{a['label']}]({a['url']})" for a in ATTRIBUTION.values()) + \
        "（いずれも本アプリで編集・加工して表示。内容は国・自治体が保証するものではありません）"
