import streamlit as st
import pandas as pd
from db import query_records
from ui import icon_title

# 登录守卫：未登录跳回主页
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()


icon_title("assets/icons/错题本.svg", "错题本")

c1, c2, c3 = st.columns(3)
with c1:
    f_error = st.selectbox("按错因筛选", ["全部","A1","A2","A3","B1","B2","B3","C1","C2","C3","UNKNOWN"])
with c2:
    start_date = st.date_input("开始日期", value=None)
with c3:
    end_date = st.date_input("结束日期", value=None)

rows = query_records(
    student_id=st.session_state.get("student_id"),
    error_code=f_error,
    start_date=str(start_date) if start_date else None,
    end_date=str(end_date) if end_date else None,
)

if not rows:
    st.info("暂无记录")
else:
    df = pd.DataFrame(rows, columns=["id","学生姓名","题目","学生步骤","错因","时间"])
    st.dataframe(df, use_container_width=True)
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出 CSV", data=csv_bytes, file_name="wrongbook_export.csv", mime="text/csv")