"""模块2 摘要器：论文纯文本 → 7 项结构化摘要。

职责边界：只做「文本 → 摘要」的组装与推理，不做文件 IO、不碰 HTTP 编排。
数据契约：``str``(论文全文) → ``dict``(7 字段，键固定见 ``DIMENSIONS``)。

长文处理：手工 Map-Reduce。全文 token 低于 ``DIRECT_TOKEN_LIMIT`` 时一次调用
直接出结果；否则按章节切段，各段先提炼要点（Map），再把各段要点汇总二次摘要
（Reduce），避免超长上下文被截断导致的信息丢失。

章节切段依据模块1（parser）输出的 ``[§] `` 标题提示，``[第 N 页]`` 页码标记
仅作噪声剔除。纯文本无 ``[§]`` 结构时退化为整篇，并套用 ``MAX_SECTION_TOKENS``
上限（超限时按空行分段再切）。

推理引擎为 DeepSeek JSON mode（OpenAI 兼容协议）。JSON 解析失败时先尝试修复
（剥 ``` 围栏、截取首个 ``{...}`` 对象）；仍失败抛 ``SummarizerError``，由编排器兜底。
"""

from __future__ import annotations

import json
import os
import re

# DeepSeek 兼容 OpenAI 协议
BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

# 全文低于该 token 数直接一次摘要，否则走 Map-Reduce
DIRECT_TOKEN_LIMIT = 8_000
# 单个 Map 单元（章节/分段）的 token 上限，超过则按空行再切
MAX_SECTION_TOKENS = 15_000

# 输出固定 7 字段；顺序即前端展示顺序
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("research_question", "研究问题"),
    ("research_background", "研究背景"),
    ("research_method", "研究方法"),
    ("conclusion", "结论"),
    ("innovation", "创新点"),
    ("limitations", "局限性"),
    ("value_to_me", "对我的价值"),
)
DIM_LABELS = dict(DIMENSIONS)

# 章节标题关键词 → 该章节主要贡献的摘要维度（见 CLAUDE.md 对应表）
_SECTION_DIMENSIONS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"abstract|摘要", re.I),
     ("research_question", "innovation", "conclusion")),
    (re.compile(r"introduction|引言|绪论|background|背景|related\s*work|相关工作", re.I),
     ("research_question", "research_background", "innovation")),
    (re.compile(r"method|approach|方法|实验|experiment", re.I),
     ("research_method",)),
    (re.compile(r"result|conclusion|结论", re.I),
     ("conclusion", "innovation")),
    (re.compile(r"discussion|limitation|讨论|局限", re.I),
     ("limitations",)),
)

"""MAP 单元标题缺失或不匹配上述关键词时，交给模型按全部维度提炼。"""
_ALL_DIMS = tuple(key for key, _ in DIMENSIONS)

_PAGE_MARKER = re.compile(r"^\s*\[第\s*\d+\s*页\]\s*$")
_HEADING_PREFIX = "[§] "

_SYSTEM = (
    "你是学术论文结构化摘要助手。只依据给定文本作答，绝不臆造；"
    "无法从文本推出的信息标注「缺失」。所有内容用中文，每项 2-3 句。"
)


class SummarizerError(Exception):
    """摘要生成失败（输入为空、缺 API Key、模型返回无法解析等）。"""


def summarize(text: str, research_direction: str = "") -> dict[str, str]:
    """把论文纯文本摘要为 7 字段字典。

    Args:
        text: 论文全文（可为 parser 输出，含 ``[§]``/``[第 N 页]`` 标记）。
        research_direction: 用户研究方向，用于个性化生成「对我的价值」。

    Returns:
        键为 ``DIMENSIONS`` 中 7 个英文名的字典，值为中文文本（缺失项为「缺失」）。

    Raises:
        SummarizerError: 输入为空、未配置 API Key、或模型输出无法解析。
    """
    if not text or not text.strip():
        raise SummarizerError("输入文本为空")

    if _count_tokens(text) <= DIRECT_TOKEN_LIMIT:
        raw = _chat(_SYSTEM, _direct_prompt(text, research_direction))
        return _normalize(_parse_json(raw))

    units = [
        (title, body)
        for title, body in _split_sections(text)
        for title, body in _chunk_section(title, body)
    ]
    partials = [_map_unit(title, body) for title, body in units]
    raw = _chat(_SYSTEM, _reduce_prompt(partials, research_direction))
    return _normalize(_parse_json(raw))


# ── 提示词 ──────────────────────────────────────────────────────────────

def _direct_prompt(text: str, research_direction: str) -> str:
    return (
        "请阅读以下论文全文，输出 7 项结构化摘要。\n\n"
        f"{_dimension_list()}\n\n"
        f"我的研究方向：{research_direction or '未提供'}\n\n"
        "以 JSON 对象输出，键为上述英文名，值为中文文本。\n\n"
        f"论文全文：\n{text}"
    )


def _map_prompt(title: str, body: str) -> str:
    dims = _dimensions_for(title)
    listing = "\n".join(f"- {key} {DIM_LABELS[key]}" for key in dims)
    return (
        "以下是论文的一个章节片段。请只针对与该片段相关的维度提炼要点，"
        "每维 1-2 句中文；未涉及的维度不要输出。\n\n"
        f"相关维度：\n{listing}\n\n"
        "以 JSON 对象输出，键为维度英文名，值为中文要点。\n\n"
        f"章节标题：{title or '（无标题）'}\n内容：\n{body}"
    )


def _reduce_prompt(partials: list[dict], research_direction: str) -> str:
    return (
        "以下是同一篇论文各章节分别提炼的要点（JSON 数组）。"
        "请汇总为最终的 7 项结构化摘要，每项 2-3 句中文；"
        "文本未提供的信息标注「缺失」。「对我的价值」需结合我的研究方向。\n\n"
        f"{_dimension_list()}\n\n"
        f"我的研究方向：{research_direction or '未提供'}\n\n"
        "以 JSON 对象输出，键为上述 7 个英文名。\n\n"
        f"各章节要点：\n{json.dumps(partials, ensure_ascii=False)}"
    )


def _dimension_list() -> str:
    return "摘要项：\n" + "\n".join(
        f"- {key} {label}" for key, label in DIMENSIONS
    )


# ── 章节切段 ────────────────────────────────────────────────────────────

def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 ``[§] `` 标题切段；无标题时退化为单段整篇。

    ``[第 N 页]`` 行作为噪声剔除。
    """
    sections: list[tuple[str, str]] = []
    title = ""
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if title or joined:
            sections.append((title, joined))

    for line in text.splitlines():
        if _PAGE_MARKER.match(line):
            continue
        if line.startswith(_HEADING_PREFIX):
            flush()
            title = line[len(_HEADING_PREFIX):].strip()
            body = []
        else:
            body.append(line)
    flush()
    return sections or [("", text.strip())]


def _chunk_section(title: str, body: str) -> list[tuple[str, str]]:
    """单段超过 ``MAX_SECTION_TOKENS`` 时按空行分块。"""
    if _count_tokens(body) <= MAX_SECTION_TOKENS:
        return [(title, body)]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in body.split("\n\n"):
        para_size = _count_tokens(para)
        if current and size + para_size > MAX_SECTION_TOKENS:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(para)
        size += para_size
    if current:
        chunks.append("\n\n".join(current))
    return [(title, chunk) for chunk in chunks]


def _dimensions_for(title: str) -> tuple[str, ...]:
    dims: list[str] = []
    for pattern, keys in _SECTION_DIMENSIONS:
        if pattern.search(title):
            dims.extend(keys)
    return tuple(dict.fromkeys(dims)) or _ALL_DIMS


# ── DeepSeek 调用 ───────────────────────────────────────────────────────

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise SummarizerError("未设置环境变量 DEEPSEEK_API_KEY")
        from openai import OpenAI

        _CLIENT = OpenAI(api_key=key, base_url=BASE_URL)
    return _CLIENT


def _chat(system: str, user: str) -> str:
    """调用 DeepSeek JSON mode，返回模型原始文本。"""
    try:
        resp = _client().chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=1.3,
        )
    except SummarizerError:
        raise
    except Exception as exc:  # openai 的各类网络/鉴权/限流异常
        raise SummarizerError(f"DeepSeek 调用失败：{exc}") from exc
    return resp.choices[0].message.content or ""


def _map_unit(title: str, body: str) -> dict:
    raw = _chat(_SYSTEM, _map_prompt(title, body))
    return _parse_json(raw)


# ── 解析与归一化 ────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)  # 剥离 ``` 围栏或前后杂讯
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise SummarizerError(f"摘要 JSON 解析失败：{raw[:200]}")


def _normalize(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, _ in DIMENSIONS:
        value = data.get(key)
        out[key] = str(value).strip() if value else "缺失"
    return out


# ── Token 统计（cl100k_base 近似 DeepSeek 分词）────────────────────────

_ENCODER = None


def _count_tokens(text: str) -> int:
    global _ENCODER
    if _ENCODER is None:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return len(_ENCODER.encode(text))


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:  # 调试便利：给了文件路径就读文件，否则读 stdin
        with open(sys.argv[1], encoding="utf-8") as fh:
            _text = fh.read()
    else:
        _text = sys.stdin.read()
    print(json.dumps(summarize(_text), ensure_ascii=False, indent=2))
