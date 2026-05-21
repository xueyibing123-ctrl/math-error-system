import streamlit as st
import pandas as pd
from db import query_records
from ui import icon_title

if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

icon_title("assets/icons/错题本.svg", "错题本")

user = st.session_state.get("user", {})
role = user.get("role", "student")
# 关键修复：用 username 查询，与批量分析存储的 student_id 一致
username = user.get("username", "")

# 教师可查所有学生，学生只查自己
c1, c2, c3, c4 = st.columns(4)
with c1:
    if role == "teacher":
        stu_filter = st.text_input("学生姓名（空=全部）", placeholder="留空查所有学生")
    else:
        stu_filter = username
        st.caption(f"当前学生：{username}")
with c2:
    f_error = st.selectbox("按错因筛选", ["ALL", "A1", "A2", "A3", "B1", "B2", "B3",
                                          "C1", "C2", "C3", "UNKNOWN"])
with c3:
    start_date = st.date_input("开始日期", value=None)
with c4:
    end_date = st.date_input("结束日期", value=None)

rows = query_records(
    student_id=stu_filter if stu_filter else None,
    error_code=f_error if f_error != "ALL" else None,
    start_date=str(start_date) if start_date else None,
    end_date=str(end_date) if end_date else None,
    limit=200,
)

if not rows:
    st.info("暂无错题记录。完成批量分析后，系统会自动将错题存入这里。")
    st.stop()

# rows: (id, student_id, question, student_answer, error_tag, created_at)
ERROR_DESC = {
    "A1": "抄写错误", "A2": "过程错误", "A3": "基础薄弱",
    "B1": "概念错误", "B2": "方法误判", "B3": "迁移失败",
    "C1": "综合困难", "C2": "未作答/畏难", "C3": "抽象不足",
    "UNKNOWN": "未分类",
}
TAG_COLOR = {
    "A1": "🟡", "A2": "🟠", "A3": "🔴",
    "B1": "🔴", "B2": "🟠", "B3": "🟠",
    "C1": "🔴", "C2": "🟡", "C3": "🟠",
    "UNKNOWN": "⚪",
}

st.markdown(f"共 **{len(rows)}** 条错题记录")

# 统计面板
df_all = pd.DataFrame(rows, columns=["id", "学生", "题目", "学生答案", "错因", "时间"])
err_counts = df_all["错因"].value_counts()
if not err_counts.empty:
    cols_stat = st.columns(min(len(err_counts), 5))
    for col, (tag, cnt) in zip(cols_stat, err_counts.items()):
        desc = ERROR_DESC.get(tag, tag)
        col.metric(f"{TAG_COLOR.get(tag,'')} {tag}·{desc}", f"{cnt} 题")

st.divider()

# 已复习 session state
reviewed_key = "reviewed_ids"
if reviewed_key not in st.session_state:
    st.session_state[reviewed_key] = set()

# 逐题展示
for row in rows:
    rid, stu, question, student_ans, error_tag, created_at = row
    tag_icon = TAG_COLOR.get(error_tag, "⚪")
    tag_desc = ERROR_DESC.get(error_tag, error_tag)
    is_reviewed = rid in st.session_state[reviewed_key]
    reviewed_badge = " ✅已复习" if is_reviewed else ""

    short_q = question[:35] + "…" if len(question) > 35 else question
    header = f"{tag_icon} **{error_tag}·{tag_desc}**　{short_q}{reviewed_badge}"

    with st.expander(header, expanded=False):
        col_q, col_btn = st.columns([5, 1])
        with col_q:
            if role == "teacher":
                st.caption(f"学生：{stu}　时间：{str(created_at)[:16]}")
            else:
                st.caption(f"时间：{str(created_at)[:16]}")
        with col_btn:
            if is_reviewed:
                if st.button("↩ 取消复习", key=f"unrev_{rid}"):
                    st.session_state[reviewed_key].discard(rid)
                    st.rerun()
            else:
                if st.button("✅ 标记已复习", key=f"rev_{rid}"):
                    st.session_state[reviewed_key].add(rid)
                    st.rerun()

        st.markdown("**📝 题目：**")
        st.markdown(f"> {question}")

        st.markdown("**✏️ 学生答案：**")
        st.markdown(f"> {student_ans or '（未作答）'}")

        # 错因说明
        st.markdown(
            f"**❌ 错因：** `{error_tag}` — {tag_desc}　"
            f"{'建议加强基础练习' if error_tag.startswith('A') else '建议复习相关概念' if error_tag.startswith('B') else '建议降低难度逐步引导'}"
        )

st.divider()

# 导出
df_export = pd.DataFrame(rows, columns=["id", "学生", "题目", "学生答案", "错因", "时间"])
df_export = df_export.drop(columns=["id"])
csv_bytes = df_export.to_csv(index=False).encode("utf-8-sig")
st.download_button("⬇️ 导出错题 CSV", data=csv_bytes,
                   file_name="错题本.csv", mime="text/csv")
