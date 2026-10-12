"""模块5 格式化器：结构化摘要 → Markdown。

职责边界：只做「7 字段 dict → Markdown 字符串」的排版，不做 IO、不调模型。
数据契约：``list[(标题, 7 字段 dict)]`` → ``str``(Markdown)。

字段的中文标签与顺序取自模块2 的 ``DIMENSIONS``（单一事实来源，避免两处维护）。
支持单篇（``title`` 为空）与多篇（每篇一个标题块，之间以水平线分隔）。
"""

from __future__ import annotations

from summarizer import DIMENSIONS


def to_markdown(entries: list[tuple[str, dict]], *, level: int = 1) -> str:
    """把一篇或多篇摘要渲染为 Markdown。

    Args:
        entries: ``[(标题, 摘要 dict)]``；标题为空时省略该篇的标题行。
        level: 每篇标题的起始层级（默认 ``#``），字段标题依次低一级。

    Returns:
        以换行结尾的 Markdown 文本。多篇之间用 ``---`` 分隔。
    """
    blocks = [_render_one(title, summary, level) for title, summary in entries]
    if len(blocks) > 1:
        return "\n\n---\n\n".join(blocks).rstrip() + "\n"
    return "\n\n".join(blocks).rstrip() + "\n"


def _render_one(title: str, summary: dict, level: int) -> str:
    field_level = "#" * (level + 1)
    parts: list[str] = []
    if title:
        parts.append(f"{'#' * level} {title}")
    for key, label in DIMENSIONS:
        parts.append(f"{field_level} {label}\n\n{summary.get(key) or '缺失'}")
    return "\n\n".join(parts)
