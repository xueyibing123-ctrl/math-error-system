import os
import json
import streamlit as st
from db import query_records
from llm_client import chat
from ui import icon_title
from dotenv import load_dotenv

load_dotenv()

if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

icon_title("assets/icons/错题本.svg", "错题本")

user = st.session_state.get("user", {})
role = user.get("role", "student")
username = user.get("username", "")

# ── 错因元数据 ─────────────────────────────────────────
ERROR_DESC = {
    "A1": "抄写错误", "A2": "过程错误", "A3": "基础薄弱",
    "B1": "概念错误", "B2": "方法误判", "B3": "迁移失败",
    "C1": "综合困难", "C2": "未作答/畏难", "C3": "抽象不足",
    "UNKNOWN": "未分类",
}
TAG_COLOR = {
    "A1": "#f59e0b", "A2": "#f97316", "A3": "#ef4444",
    "B1": "#ef4444", "B2": "#f97316", "B3": "#f97316",
    "C1": "#ef4444", "C2": "#f59e0b", "C3": "#f97316",
    "UNKNOWN": "#9ca3af",
}
CATEGORY_FILTER = {
    "全部": None,
    "A类·计算类": ["A1", "A2", "A3"],
    "B类·概念类": ["B1", "B2", "B3"],
    "C类·理解类": ["C1", "C2", "C3"],
    "未分类": ["UNKNOWN"],
}

# ── Session State ─────────────────────────────────────
for _k, _v in {
    "wb_mastered": set(),
    "wb_redo_mode": set(),
    "wb_redo_results": {},
    "wb_category": "全部",
    "wb_show_mastered": True,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── 顶部筛选区 ─────────────────────────────────────────
# 教师搜索框 & 日期（折叠放右边）
with st.expander("🔍 筛选条件", expanded=False):
    fc1, fc2, fc3 = st.columns([2, 1.5, 1.5])
    with fc1:
        if role == "teacher":
            stu_filter = st.text_input("学生姓名（空=全部）", placeholder="留空查所有学生")
        else:
            stu_filter = username
            st.caption(f"当前学生：{username}")
    with fc2:
        start_date = st.date_input("开始日期", value=None)
    with fc3:
        end_date = st.date_input("结束日期", value=None)

if role == "student":
    stu_filter = username

# ── 加载数据 ──────────────────────────────────────────
rows = query_records(
    student_id=stu_filter if stu_filter else None,
    start_date=str(start_date) if start_date else None,
    end_date=str(end_date) if end_date else None,
    limit=500,
)
# rows: (id, student_id, question, student_answer, error_tag, created_at)

if not rows:
    st.info("暂无错题记录。完成作业批改后，错题会自动记录在这里。")
    st.stop()

total_all = len(rows)
mastered_n = len([r for r in rows if r[0] in st.session_state.wb_mastered])

# ── 统计概览卡片 ──────────────────────────────────────
mc1, mc2, mc3 = st.columns(3)
mc1.metric("错题总数", f"{total_all} 题")
mc2.metric("已掌握", f"{mastered_n} 题")
mc3.metric("待攻克", f"{total_all - mastered_n} 题")

# ── 错因分类 Pill 筛选 ────────────────────────────────
st.markdown("**按错因分类筛选：**")

pill_cols = st.columns(len(CATEGORY_FILTER))
for i, cat in enumerate(CATEGORY_FILTER):
    is_active = st.session_state.wb_category == cat
    style = (
        "background:#FF4B4B;color:white;" if is_active
        else "background:#f0f2f6;color:#333;"
    )
    if pill_cols[i].button(
        cat,
        key=f"cat_{cat}",
        use_container_width=True,
        type="primary" if is_active else "secondary",
    ):
        st.session_state.wb_category = cat
        st.rerun()

# ── 按错因分类内的细分标签 ────────────────────────────
selected_cat = st.session_state.wb_category
cat_tags = CATEGORY_FILTER[selected_cat]  # None = 全部, list = 具体标签

# 在分类内再细分（显示该分类下各标签 pill）
if cat_tags:
    all_in_cat = ["全部" + selected_cat[1:]] + cat_tags
    if "wb_subtag" not in st.session_state or st.session_state.get("wb_last_cat") != selected_cat:
        st.session_state.wb_subtag = None
        st.session_state.wb_last_cat = selected_cat
    sub_cols = st.columns(len(all_in_cat))
    for i, tag in enumerate(all_in_cat):
        real_tag = None if i == 0 else tag
        is_sel = st.session_state.wb_subtag == real_tag
        if sub_cols[i].button(
            f"{tag}·{ERROR_DESC.get(tag,'全部')}" if i > 0 else "全部",
            key=f"sub_{tag}",
            use_container_width=True,
            type="primary" if is_sel else "secondary",
        ):
            st.session_state.wb_subtag = real_tag
            st.rerun()
else:
    st.session_state.wb_subtag = None

# ── 过滤数据 ──────────────────────────────────────────
def passes_filter(row):
    rid, stu, q, ans, tag, ts = row
    # 分类过滤
    if cat_tags and tag not in cat_tags:
        return False
    # 细分标签过滤
    if st.session_state.wb_subtag and tag != st.session_state.wb_subtag:
        return False
    # 已掌握过滤
    if not st.session_state.wb_show_mastered and rid in st.session_state.wb_mastered:
        return False
    return True

filtered = [r for r in rows if passes_filter(r)]

# ── 显示控制 ──────────────────────────────────────────
ctrl1, ctrl2 = st.columns([3, 1])
with ctrl1:
    st.markdown(f"当前显示 **{len(filtered)}** 条 / 共 {total_all} 条")
with ctrl2:
    show_m = st.toggle("显示已掌握", value=st.session_state.wb_show_mastered, key="show_mastered_toggle")
    if show_m != st.session_state.wb_show_mastered:
        st.session_state.wb_show_mastered = show_m
        st.rerun()

if not filtered:
    st.success("🎉 该分类下暂无错题，太棒了！")
    st.stop()

st.divider()

# ── 重做批改 Prompt ───────────────────────────────────
_REDO_SYSTEM = """你是作业批改助手。学生正在重做一道曾经做错的题目。请批改学生的新答案。

【批改宽松原则】
- 时间单位缩写等价：分=分钟、秒=秒钟、时=小时，不扣分
- 数字与汉字等价：1=一、0.5=二分之一，不扣分
- 语文/英语主观题：意思对即算对，不强求用词完全一致
- 计算步骤有小笔误但结果正确，酌情判对

严格输出JSON，不输出其他文字：
{"correct": true或false, "feedback": "若正确：真诚鼓励30字内；若错误：苏格拉底式引导100字内不直接给答案", "error_tag": "仅答错时填：A1/A2/A3/B1/B2/B3/C1/C2/C3"}""".strip()


def grade_redo(question: str, new_answer: str) -> dict:
    model = os.getenv("DASHSCOPE_MODEL", "qwen-max")
    user_prompt = f"题目：{question}\n\n学生新答案：{new_answer}"
    try:
        raw = chat(model=model, system=_REDO_SYSTEM, user=user_prompt, temperature=0.2)
        raw = raw.strip()
        # JSON 提取
        try:
            return json.loads(raw)
        except Exception:
            import re
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                return json.loads(m.group())
        return {"correct": False, "feedback": raw, "error_tag": ""}
    except Exception as e:
        return {"correct": False, "feedback": f"批改失败：{e}", "error_tag": ""}


# ── 逐题卡片 ─────────────────────────────────────────
for row in filtered:
    rid, stu, question, student_ans, error_tag, created_at = row
    tag_desc = ERROR_DESC.get(error_tag, error_tag)
    tag_color = TAG_COLOR.get(error_tag, "#9ca3af")
    is_mastered = rid in st.session_state.wb_mastered
    redo_result = st.session_state.wb_redo_results.get(rid)
    redo_correct = redo_result and redo_result.get("correct")

    # 卡片顶部状态标识
    if is_mastered:
        status_badge = "✅ 已掌握"
        border_color = "#21c45d"
    elif redo_result and redo_correct:
        status_badge = "🎉 重做正确"
        border_color = "#21c45d"
    elif redo_result:
        status_badge = "🔄 重做有误"
        border_color = "#f97316"
    else:
        status_badge = ""
        border_color = tag_color

    short_q = question[:42] + "…" if len(question) > 42 else question

    # 卡片标题行
    badge_html = (
        f'<span style="background:{tag_color};color:white;padding:2px 8px;'
        f'border-radius:10px;font-size:12px;font-weight:bold;margin-right:6px;">'
        f'{error_tag}·{tag_desc}</span>'
    )
    if status_badge:
        badge_html += (
            f'<span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;'
            f'border-radius:10px;font-size:12px;">{status_badge}</span>'
        )

    expander_label = f"{error_tag}·{tag_desc}　{short_q}"
    if status_badge:
        expander_label += f"　{status_badge}"

    with st.expander(expander_label, expanded=False):
        # 元信息
        meta_parts = []
        if role == "teacher":
            meta_parts.append(f"学生：**{stu}**")
        meta_parts.append(f"时间：{str(created_at)[:16]}")
        st.caption("　｜　".join(meta_parts))

        st.markdown(badge_html, unsafe_allow_html=True)
        st.markdown("---")

        # 题目 & 原答案
        st.markdown("**📝 题目**")
        st.markdown(f"> {question}")

        st.markdown("**✏️ 学生原答案**")
        st.markdown(f"> {student_ans or '（未作答）'}")

        # 错因说明
        if error_tag != "UNKNOWN":
            if error_tag.startswith("A"):
                advice = "建议加强基础计算练习，认真检验每一步"
            elif error_tag.startswith("B"):
                advice = "建议复习相关概念和公式，理解本质"
            else:
                advice = "建议降低难度逐步引导，培养解题思路"
            st.markdown(
                f"**❌ 错因：** `{error_tag}` — {tag_desc}　｜　💡 {advice}"
            )

        st.markdown("---")

        # ── 操作按钮区 ────────────────────────────────
        if is_mastered:
            # 已掌握状态
            col_btn1, col_btn2 = st.columns([1, 1])
            with col_btn1:
                st.success("✅ 已标记掌握")
            with col_btn2:
                if st.button("↩ 取消掌握", key=f"unmaster_{rid}"):
                    st.session_state.wb_mastered.discard(rid)
                    st.rerun()
        else:
            col_btn1, col_btn2 = st.columns([1, 1])
            with col_btn1:
                if st.button("✅ 标记已掌握", key=f"master_{rid}", use_container_width=True):
                    st.session_state.wb_mastered.add(rid)
                    st.session_state.wb_redo_mode.discard(rid)
                    st.rerun()
            with col_btn2:
                in_redo = rid in st.session_state.wb_redo_mode
                if in_redo:
                    if st.button("✕ 取消重做", key=f"cancel_redo_{rid}", use_container_width=True):
                        st.session_state.wb_redo_mode.discard(rid)
                        st.rerun()
                else:
                    if st.button("✏️ 重做这道题", key=f"redo_{rid}",
                                 use_container_width=True, type="primary"):
                        st.session_state.wb_redo_mode.add(rid)
                        st.rerun()

        # ── 重做区域 ──────────────────────────────────
        if rid in st.session_state.wb_redo_mode and not is_mastered:
            st.markdown("**🖊️ 写下你的新答案：**")
            new_ans = st.text_area(
                "新答案", label_visibility="collapsed",
                height=100, key=f"redo_text_{rid}",
                placeholder="请写出完整解题过程或答案…"
            )
            if st.button("🚀 提交批改", key=f"submit_redo_{rid}", type="primary"):
                if not new_ans.strip():
                    st.warning("请先填写新答案")
                else:
                    with st.spinner("AI批改中…"):
                        result = grade_redo(question, new_ans.strip())
                    st.session_state.wb_redo_results[rid] = result
                    st.session_state.wb_redo_mode.discard(rid)
                    st.rerun()

        # ── 重做结果展示 ─────────────────────────────
        if redo_result and not is_mastered:
            correct = redo_result.get("correct", False)
            feedback = redo_result.get("feedback", "")
            new_tag = redo_result.get("error_tag", "")
            if correct:
                st.success("🎉 重做正确！")
                st.info(feedback)
                if st.button("🏆 标记为已掌握", key=f"master_after_redo_{rid}", type="primary"):
                    st.session_state.wb_mastered.add(rid)
                    st.session_state.wb_redo_results.pop(rid, None)
                    st.rerun()
            else:
                _err_hint = f"，错因：`{new_tag}`·{ERROR_DESC.get(new_tag, '')}" if new_tag else ""
                st.error(f"❌ 还有不足{_err_hint}")
                st.warning(feedback)
                if st.button("🔄 再试一次", key=f"retry_redo_{rid}"):
                    st.session_state.wb_redo_mode.add(rid)
                    st.session_state.wb_redo_results.pop(rid, None)
                    st.rerun()

st.divider()

# ── 底部统计 & 导出 ───────────────────────────────────
import pandas as pd
df_export = pd.DataFrame(
    [(r[1], r[2], r[3], r[4], str(r[5])[:16]) for r in rows],
    columns=["学生", "题目", "学生答案", "错因", "时间"]
)
col_exp1, col_exp2 = st.columns([1, 1])
with col_exp1:
    csv_bytes = df_export.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ 导出错题 CSV", data=csv_bytes,
        file_name="错题本.csv", mime="text/csv",
        use_container_width=True
    )
with col_exp2:
    if st.button("🗑️ 清空本次掌握记录", use_container_width=True):
        st.session_state.wb_mastered = set()
        st.session_state.wb_redo_results = {}
        st.rerun()
