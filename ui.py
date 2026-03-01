import re
import streamlit as st


def load_svg(path: str, size: int = 24, color: str = "#4F6CF7") -> str:
    """读取 SVG 文件并注入尺寸与颜色，返回 SVG 字符串；失败返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            svg = f.read()

        # 替换/注入 width & height
        if 'width=' in svg:
            svg = re.sub(r'width="[^"]*"', f'width="{size}"', svg, count=1)
        else:
            svg = svg.replace("<svg", f'<svg width="{size}"', 1)

        if 'height=' in svg:
            svg = re.sub(r'height="[^"]*"', f'height="{size}"', svg, count=1)
        else:
            svg = svg.replace("<svg", f'<svg height="{size}"', 1)

        # 替换 currentColor
        svg = svg.replace("currentColor", color)

        return svg
    except Exception:
        return ""


def icon_title(icon_path: str, title: str, size: int = 28, color: str = "#4F6CF7") -> None:
    """在页面顶部渲染「SVG图标 + 标题」横排布局。"""
    svg = load_svg(icon_path, size=size, color=color)
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:0.6rem; margin-bottom:0.25rem;">
            {svg}
            <h1 style="margin:0; font-size:1.7rem; color:#1A1D2E;">{title}</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )


def icon_text(icon_path: str, text: str, size: int = 18, color: str = "#4F6CF7") -> None:
    """渲染「SVG图标 + 普通文字」横排布局，常用于侧边栏。"""
    svg = load_svg(icon_path, size=size, color=color)
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:0.5rem; padding:0.2rem 0;">
            {svg}
            <span style="font-size:0.95rem; color:#2D3250; font-weight:500;">{text}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )