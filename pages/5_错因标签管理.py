import streamlit as st
import pandas as pd
from db import get_all_error_tags, upsert_error_tag, delete_error_tag
from ui import icon_title

# 登录守卫
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

if st.session_state.get("user", {}).get("role") != "teacher":
    st.error("此页面仅教师可访问")
    st.stop()

icon_title("assets/icons/错因管理标签.svg", "错因标签管理")
st.caption("管理错因标签体系，设置专项训练触发阈值。修改后AI分析时会作为参考。")

tags = get_all_error_tags()
df = pd.DataFrame(tags, columns=["ID", "编码", "名称", "说明", "训练阈值", "触发训练"])
df["触发训练"] = df["触发训练"].apply(lambda x: "✅ 是" if x else "❌ 否")

tab1, tab2 = st.tabs(["📋 标签列表", "➕ 新增/编辑标签"])

with tab1:
    st.subheader("当前错因标签体系")
    st.dataframe(df, use_container_width=True)

    st.divider()
    st.subheader("🗑️ 删除标签")
    st.caption("⚠️ 删除后已有记录不受影响，但AI分析时不再使用该标签。")

    codes = [row[1] for row in tags]
    del_code = st.selectbox("选择要删除的标签编码", codes, key="del_select")
    if st.button("删除该标签", key="btn_delete", type="primary"):
        if del_code in ["A1","A2","A3","B1","B2","B3","C1","C2","C3"]:
            st.error("默认标签不可删除，只能编辑。")
        else:
            delete_error_tag(del_code)
            st.success(f"已删除标签 {del_code}，刷新页面生效。")

with tab2:
    st.subheader("新增或编辑标签")
    st.caption("输入已有编码则为编辑，输入新编码则为新增。")

    col1, col2 = st.columns(2)
    with col1:
        code = st.text_input("标签编码（如 D1）", max_chars=5)
    with col2:
        name = st.text_input("标签名称（如 审题不仔细）")

    description = st.text_area("说明（一句话描述）", height=80)

    col3, col4 = st.columns(2)
    with col3:
        threshold = st.number_input("触发专项训练阈值（次数）", min_value=1, max_value=20, value=3)
    with col4:
        enable = st.checkbox("启用专项训练", value=True)

    if st.button("保存标签", type="primary", key="btn_save_tag"):
        if not code or not name:
            st.warning("编码和名称不能为空")
        else:
            upsert_error_tag(code.strip().upper(), name.strip(), description.strip(), threshold, enable)
            st.success(f"✅ 标签 {code.upper()} 已保存！刷新页面查看。")

    st.divider()
    st.subheader("📖 快速编辑已有标签")
    edit_code = st.selectbox("选择要编辑的标签", [row[1] for row in tags], key="edit_select")
    selected = next((row for row in tags if row[1] == edit_code), None)
    if selected:
        _, _, s_name, s_desc, s_threshold, s_enable = selected
        new_name = st.text_input("名称", value=s_name, key="edit_name")
        new_desc = st.text_area("说明", value=s_desc or "", key="edit_desc", height=80)
        col5, col6 = st.columns(2)
        with col5:
            new_threshold = st.number_input("阈值", min_value=1, max_value=20,
                                            value=int(s_threshold), key="edit_threshold")
        with col6:
            new_enable = st.checkbox("启用专项训练", value=bool(s_enable), key="edit_enable")
        if st.button("保存修改", key="btn_edit_save"):
            upsert_error_tag(edit_code, new_name, new_desc, new_threshold, new_enable)
            st.success(f"✅ 标签 {edit_code} 已更新！刷新页面查看。")