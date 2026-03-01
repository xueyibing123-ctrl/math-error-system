import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from db import get_all_students, get_student_error_stats, get_student_trend, get_recent_records
from ui import icon_title

# 登录守卫
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

icon_title("assets/icons/学生画像.svg", "学生画像")

user = st.session_state.get("user", {})
role = user.get("role", "student")

if role == "teacher":
    students = get_all_students()
    if not students:
        st.info("暂无学生数据")
        st.stop()
    teacher_class = st.session_state.get("user", {}).get("class_name")
    if teacher_class:
        students = [s for s in students if s[2] == teacher_class]
        st.caption(f"当前显示：**{teacher_class}** 的学生")
    names = [row[1] for row in students]
    selected_name = st.selectbox("选择学生", names)
else:
    selected_name = user.get("username")
    st.caption(f"当前查看：**{selected_name}** 的学习画像")

error_stats = get_student_error_stats(selected_name)
trend_rows = get_student_trend(selected_name, days=30)

if not error_stats:
    st.info(f"「{selected_name}」暂无答题记录，完成错因分析后此处会自动生成画像。")
    st.stop()

df_error = pd.DataFrame(error_stats, columns=["错因", "次数"])
total = df_error["次数"].sum()

st.divider()
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("总答题数", total)
with col2:
    st.metric("错因种类", len(df_error))
with col3:
    top_error = df_error.iloc[0]["错因"]
    st.metric("最高频错因", top_error)
with col4:
    high_risk = df_error[df_error["次数"] >= 3]
    st.metric("需重点关注", f"{len(high_risk)} 项",
              delta="需干预" if len(high_risk) > 0 else None, delta_color="inverse")

st.divider()
col_radar, col_bar = st.columns(2)
ALL_TAGS = ["A1","A2","A3","B1","B2","B3","C1","C2","C3"]

with col_radar:
    st.subheader("🕸️ 错因雷达图")
    tag_dict = dict(zip(df_error["错因"], df_error["次数"]))
    radar_values = [tag_dict.get(t, 0) for t in ALL_TAGS]
    fig_radar = go.Figure(data=go.Scatterpolar(
        r=radar_values + [radar_values[0]],
        theta=ALL_TAGS + [ALL_TAGS[0]],
        fill="toself",
        fillcolor="rgba(79, 108, 247, 0.2)",
        line=dict(color="#4F6CF7", width=2),
        marker=dict(size=6, color="#4F6CF7"),
    ))
    fig_radar.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, max(radar_values) + 1])),
        showlegend=False, height=350, margin=dict(t=30, b=30, l=30, r=30),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

with col_bar:
    st.subheader("📊 错因详细分布")
    df_sorted = df_error.sort_values("次数", ascending=True)
    colors = ["#EF4444" if n >= 3 else "#4F6CF7" for n in df_sorted["次数"]]
    fig_bar = go.Figure(go.Bar(
        x=df_sorted["次数"], y=df_sorted["错因"], orientation="h",
        marker_color=colors, text=df_sorted["次数"], textposition="outside",
    ))
    fig_bar.update_layout(
        height=350, margin=dict(t=20, b=20, l=20, r=40), xaxis_title="出现次数",
        annotations=[dict(x=0.5, y=1.05, xref="paper", yref="paper",
                          text="🔴 红色 = 需重点关注（≥3次）",
                          showarrow=False, font=dict(size=11, color="#EF4444"))]
    )
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()
st.subheader("📈 近30天错因趋势")
if not trend_rows:
    st.info("近30天暂无数据")
else:
    df_trend = pd.DataFrame(trend_rows, columns=["日期", "错因", "次数"])
    fig_line = px.line(df_trend, x="日期", y="次数", color="错因", markers=True,
                       color_discrete_sequence=px.colors.qualitative.Set1,
                       labels={"日期": "日期", "次数": "次数", "错因": "错因标签"})
    fig_line.update_layout(height=350, margin=dict(t=20, b=20),
                           hovermode="x unified", legend_title="错因标签")
    st.plotly_chart(fig_line, use_container_width=True)

st.divider()
st.subheader("📝 薄弱点分析")

ERROR_DESC = {
    "A1": "数字抄写错误——做题时注意认真核对数字",
    "A2": "计算过程错误——建议多练竖式计算",
    "A3": "基础技能薄弱——需要加强基础运算训练",
    "B1": "单位/关键概念识别错误——阅读题目时圈出关键词",
    "B2": "运算类型误判——先判断用哪种运算再计算",
    "B3": "变式迁移失败——多练同类不同说法的题目",
    "C1": "综合结构理解困难——画线段图辅助理解",
    "C2": "畏难情绪放弃——鼓励先尝试，不怕错",
    "C3": "抽象关系建模不足——多练列方程或画图建模",
}

high_risk_tags = df_error[df_error["次数"] >= 3]["错因"].tolist()
if high_risk_tags:
    for tag in high_risk_tags:
        desc = ERROR_DESC.get(tag, tag)
        st.error(f"🔴 **{tag}**：{desc}（出现 {tag_dict[tag]} 次）")
else:
    st.success("🎉 暂无高频错因，继续保持！")

medium_tags = df_error[(df_error["次数"] >= 1) & (df_error["次数"] < 3)]["错因"].tolist()
if medium_tags:
    for tag in medium_tags:
        desc = ERROR_DESC.get(tag, tag)
        st.warning(f"🟡 **{tag}**：{desc}（出现 {tag_dict[tag]} 次）")