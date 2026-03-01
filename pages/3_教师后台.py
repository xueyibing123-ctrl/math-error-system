import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from db import get_recent_records, get_error_stats, list_alerts, resolve_alert
from ui import icon_title

# 登录守卫
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

if st.session_state.get("user", {}).get("role") != "teacher":
    st.error("此页面仅教师可访问")
    st.stop()

icon_title("assets/icons/教师后台.svg", "教师后台")

# ── 数据准备 ──────────────────────────────────────────
teacher_class = st.session_state.get("user", {}).get("class_name")
if teacher_class:
    from db import get_conn
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.student_id, r.question, r.student_answer,
               r.error_tag, r.feedback, r.created_at
        FROM wrong_records r
        LEFT JOIN users u ON r.student_id = u.username
        WHERE u.class_name = ?
        ORDER BY r.created_at DESC LIMIT 500
    """, (teacher_class,))
    rows = cur.fetchall()
    conn.close()
    st.caption(f"当前显示：**{teacher_class}** 的数据")
else:
    rows = get_recent_records(limit=500)

if not rows:
    st.info("暂无数据，学生完成分析后此处会显示统计信息。")
    st.stop()

df_all = pd.DataFrame(rows, columns=["id", "student_id", "question", "student_answer", "error_tag", "feedback", "created_at"])
df_all["created_at"] = pd.to_datetime(df_all["created_at"])
df_all["date"] = df_all["created_at"].dt.date

# ── Tab 布局 ──────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📊 班级总览", "📈 错因趋势", "🚨 预警管理", "📋 原始记录"])

with tab1:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总答题记录", len(df_all))
    with col2:
        st.metric("涉及学生数", df_all["student_answer"].nunique())
    with col3:
        alert_rows = list_alerts(status="OPEN")
        st.metric("未处理预警", len(alert_rows), delta="需关注" if alert_rows else None)

    st.divider()
    col_pie, col_bar = st.columns(2)

    with col_pie:
        st.subheader("错因占比")
        stats = get_error_stats(limit=20)
        if stats:
            df_stats = pd.DataFrame(stats, columns=["错因", "次数"])
            fig_pie = px.pie(df_stats, names="错因", values="次数",
                             color_discrete_sequence=px.colors.qualitative.Set3, hole=0.35)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=320)
            st.plotly_chart(fig_pie, use_container_width=True)

    with col_bar:
        st.subheader("错因排行榜")
        if stats:
            df_stats_sorted = df_stats.sort_values("次数", ascending=True)
            fig_bar = px.bar(df_stats_sorted, x="次数", y="错因", orientation="h",
                             color="次数", color_continuous_scale="Blues")
            fig_bar.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=320,
                                  showlegend=False, coloraxis_showscale=False)
            st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()
    st.subheader("🔴 高风险学生（同一错因出现 ≥3 次）")
    risk_df = (df_all.groupby(["student_answer", "error_tag"]).size()
               .reset_index(name="次数").query("次数 >= 3")
               .sort_values("次数", ascending=False)
               .rename(columns={"student_answer": "学生标识", "error_tag": "错因"}))
    if risk_df.empty:
        st.success("暂无高风险学生 🎉")
    else:
        st.dataframe(risk_df, use_container_width=True)

with tab2:
    st.subheader("📈 近期错因趋势")
    days = st.slider("显示最近几天", min_value=3, max_value=30, value=7)
    cutoff = datetime.now().date() - timedelta(days=days)
    df_recent = df_all[df_all["date"] >= cutoff]

    if df_recent.empty:
        st.info(f"最近 {days} 天暂无数据")
    else:
        df_trend = df_recent.groupby(["date", "error_tag"]).size().reset_index(name="次数")
        fig_line = px.line(df_trend, x="date", y="次数", color="error_tag", markers=True,
                           labels={"date": "日期", "error_tag": "错因", "次数": "次数"},
                           color_discrete_sequence=px.colors.qualitative.Set1)
        fig_line.update_layout(height=400, margin=dict(t=20, b=20),
                               legend_title="错因标签", hovermode="x unified")
        st.plotly_chart(fig_line, use_container_width=True)

    st.divider()
    st.subheader("🔍 查看某学生错因趋势")
    all_students = sorted(df_all["student_id"].unique().tolist())
    selected_student = st.selectbox("选择学生", all_students)
    df_stu = df_all[df_all["student_id"] == selected_student]
    df_stu_trend = df_stu.groupby(["date", "error_tag"]).size().reset_index(name="次数")
    if df_stu_trend.empty:
        st.info("该学生暂无记录")
    else:
        fig_stu = px.bar(df_stu_trend, x="date", y="次数", color="error_tag",
                         labels={"date": "日期", "error_tag": "错因"}, barmode="stack")
        fig_stu.update_layout(height=350, margin=dict(t=20, b=20))
        st.plotly_chart(fig_stu, use_container_width=True)

with tab3:
    st.subheader("🚨 未处理预警")
    alert_rows = list_alerts(status="OPEN", limit=200)
    if not alert_rows:
        st.success("暂无未处理预警 ✅")
    else:
        df_alert = pd.DataFrame(alert_rows, columns=["学生姓名", "错误类型", "次数", "阈值", "状态", "触发时间"])
        st.dataframe(df_alert, use_container_width=True)

    st.divider()
    with st.expander("✅ 处理预警"):
        col1, col2 = st.columns(2)
        with col1:
            sid = st.text_input("学生姓名（可留空）", key="resolve_sid")
        with col2:
            ecode = st.text_input("错误类型（如 B2）", key="resolve_ecode")
        if st.button("标记为已处理", key="btn_resolve"):
            resolve_alert(student_id=sid if sid else None, error_code=ecode)
            st.success("已处理，刷新页面查看最新状态。")

    st.divider()
    st.subheader("📁 已处理预警记录")
    resolved = list_alerts(status="RESOLVED", limit=50)
    if resolved:
        df_resolved = pd.DataFrame(resolved, columns=["学生姓名", "错误类型", "次数", "阈值", "状态", "触发时间"])
        st.dataframe(df_resolved, use_container_width=True)
    else:
        st.info("暂无已处理记录")

with tab4:
    st.subheader("📋 最近错题记录")
    df_show = df_all[["id", "student_id", "error_tag", "created_at"]].copy()
    df_show.columns = ["ID", "学生姓名", "错因", "时间"]
    st.dataframe(df_show, use_container_width=True)
    csv = df_show.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出全部记录", data=csv, file_name="all_records.csv", mime="text/csv")