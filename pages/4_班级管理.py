import streamlit as st
import pandas as pd
import plotly.express as px
from db import get_all_students, get_all_classes, get_records_by_class, get_error_stats
from ui import icon_title

# 登录守卫
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

if st.session_state.get("user", {}).get("role") != "teacher":
    st.error("此页面仅教师可访问")
    st.stop()

icon_title("assets/icons/school.svg", "班级管理")

tab1, tab2 = st.tabs(["👥 学生名单", "📊 班级对比"])

with tab1:
    teacher_class = st.session_state.get("user", {}).get("class_name")
    students = get_all_students()
    if teacher_class:
        students = [s for s in students if s[2] == teacher_class]
        st.caption(f"当前显示：**{teacher_class}** 的学生")
    if not students:
        st.info("暂无学生注册，学生注册后会显示在这里。")
    else:
        df_stu = pd.DataFrame(students, columns=["ID", "姓名", "班级", "注册时间"])
        classes = ["全部"] + sorted(df_stu["班级"].dropna().unique().tolist())
        selected_class = st.selectbox("按班级筛选", classes)
        df_show = df_stu[df_stu["班级"] == selected_class] if selected_class != "全部" else df_stu

        col1, col2 = st.columns(2)
        with col1:
            st.metric("学生总数", len(df_show))
        with col2:
            st.metric("班级数", df_stu["班级"].nunique())

        st.dataframe(df_show, use_container_width=True)
        csv = df_show.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ 导出名单 CSV", data=csv,
                           file_name=f"students_{selected_class}.csv", mime="text/csv")

with tab2:
    teacher_class = st.session_state.get("user", {}).get("class_name")
    classes = get_all_classes()
    if teacher_class:
        classes = [c for c in classes if c == teacher_class]
    if not classes:
        st.info("暂无班级数据，学生注册时填写班级后会显示在这里。")
    else:
        selected = st.selectbox("选择班级查看详情", classes)
        rows = get_records_by_class(selected)

        if not rows:
            st.info(f"「{selected}」暂无答题记录")
        else:
            df = pd.DataFrame(rows, columns=["ID", "学生姓名", "题目", "学生步骤", "错因", "时间", "班级"])

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("答题记录数", len(df))
            with col2:
                st.metric("参与学生数", df["学生姓名"].nunique())
            with col3:
                top_error = df["错因"].value_counts().index[0] if len(df) > 0 else "-"
                st.metric("最高频错因", top_error)

            st.divider()
            col_left, col_right = st.columns(2)

            with col_left:
                st.subheader("班级错因分布")
                error_counts = df["错因"].value_counts().reset_index()
                error_counts.columns = ["错因", "次数"]
                fig = px.pie(error_counts, names="错因", values="次数", hole=0.35,
                             color_discrete_sequence=px.colors.qualitative.Pastel)
                fig.update_layout(height=300, margin=dict(t=10, b=10))
                st.plotly_chart(fig, use_container_width=True)

            with col_right:
                st.subheader("学生错因对比")
                df_pivot = df.groupby(["学生姓名", "错因"]).size().reset_index(name="次数")
                fig2 = px.bar(df_pivot, x="学生姓名", y="次数", color="错因",
                              barmode="stack",
                              color_discrete_sequence=px.colors.qualitative.Set2)
                fig2.update_layout(height=300, margin=dict(t=10, b=10))
                st.plotly_chart(fig2, use_container_width=True)

            st.divider()
            st.subheader("🔴 班级高风险学生")
            risk = (df.groupby(["学生姓名", "错因"]).size()
                    .reset_index(name="次数").query("次数 >= 3")
                    .sort_values("次数", ascending=False))
            if risk.empty:
                st.success("该班级暂无高风险学生 🎉")
            else:
                st.dataframe(risk, use_container_width=True)

            st.divider()
            with st.expander("查看全部答题记录"):
                st.dataframe(df[["学生姓名", "错因", "时间", "题目"]], use_container_width=True)