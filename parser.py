"""模块1 解析器：PDF → 纯文本（含页码/章节提示）。

职责边界：只从 PDF 中提取文本，不做 OCR。
数据契约：``bytes``(PDF) → ``str``(全文文本)。

页码以 ``[第 N 页]`` 标记；疑似章节标题行加 ``[§] `` 前缀作为提示，
供模块2 摘要器切段参考（切段的权威判断仍在摘要器）。

版面处理：不直接用 ``page.extract_text()``。它对双栏/多栏学术版面会
（1）把左右两栏同一水平位置的行拼成一行，（2）在词序非按纵坐标单调时
把整行拆成单词级碎片。这里改为自行提取词块、按垂直重叠聚行、再按栏缝
分栏后逐栏输出。单栏或无结构页面退化为整页顺序输出。

命令行运行时，正文之后追加统计：正文字数、Token 数（cl100k_base 近似）
与每页 Token 数。统计只打印，不进 ``extract_text`` 的返回值。
"""

from __future__ import annotations

import io
import math
import re

import pdfplumber

PAGE_MARKER = "[第 {n} 页]"
HEADING_PREFIX = "[§] "

# 全文少于该字符数即判定为无文本层（扫描版）
MIN_TEXT_CHARS = 10

# 词块提取：按字号比例切词，避免小字号行内空格被吞（默认 x_tolerance=3 太宽）
_X_TOLERANCE_RATIO = 0.15
_Y_TOLERANCE = 2.0

# 同一行的判定：两词块垂直重叠比例达到该值即视为同行
_LINE_OVERLAP = 0.7

# 栏缝检测：按水平带投影，统计每个 x 被多少条带覆盖
_BAND_HEIGHT = 8.0
_GUTTER_SEARCH = (0.35, 0.65)  # 仅在页面中部该比例区间内寻找栏缝
_GUTTER_MAX_COVERAGE = 0.35  # 覆盖比例低于该值才认定为栏缝
_MIN_COLUMN_SHARE = 0.15  # 栏缝两侧各需占据的词数下限

# 编号式标题：1 / 1.2 / 1.2.3 / 第1章 / 第1节 / 一、 / （一）
_NUMBERED_HEADING = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+)*\s*[.、]?\s+\S"                      # 1 Introduction / 1.2 Method
    r"|第\s*[0-9一二三四五六七八九十]+\s*[章节部分]"      # 第3章
    r"|[一二三四五六七八九十]+\s*、"                      # 一、
    r"|[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]"        # （一）
    r")"
)

# 关键词式标题（中英），允许结尾冒号
_KEYWORD_HEADING = re.compile(
    r"^\s*(abstract|introduction|related\s+work|background|"
    r"method(?:s|ology)?|approach|experiment(?:s)?|result(?:s)?|"
    r"discussion|conclusion(?:s)?|limitation(?:s)?|references?|"
    r"摘要|引言|绪论|背景|相关工作|方法|实验|结果|讨论|结论|局限性|参考文献)"
    r"\s*[:：]?\s*$",
    re.IGNORECASE,
)

# 超过该长度的行不可能是标题
_MAX_HEADING_CHARS = 60

# 以这些标点结尾的多为正文句子，而非标题
_SENTENCE_ENDINGS = ("。", ".", "，", ",", "；", ";", "！", "!")

# 数字式标题的前导编号（1 / 1.2 / 1.2.3 及可选分隔符），用于取出标题正文
_LEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.、]?\s*")

# Title Case 判定时忽略的虚词（多为小写，不计入大写票）
_TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for", "with",
    "at", "by", "as", "from", "via", "into", "over", "under", "between",
    "using", "based", "toward", "towards", "vs", "versus", "is", "are",
    "be", "we", "our", "its", "it", "this", "that", "these", "those",
    "but", "not", "no", "than", "then", "when", "where", "which", "who",
    "how", "why", "what",
})

# 组信息/出处行常见特征：URL 或 DOI
_URL_LIKE = re.compile(r"https?://|www\.|doi", re.IGNORECASE)


class ParserError(Exception):
    """PDF 解析失败。"""


class ScannedPdfError(ParserError):
    """PDF 无文本层（扫描版），本工具不做 OCR。"""


def extract_text(pdf_bytes: bytes) -> str:
    """把 PDF 字节流提取为带页码/章节提示的纯文本。

    Raises:
        ParserError: 内容为空或 PDF 无法解析。
        ScannedPdfError: 没有可提取的文本层（扫描版）。
    """
    return _render(_parse_pages(pdf_bytes))


def _parse_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """逐页提取纯正文，返回 ``[(页码, 文本)]``（不含页码/章节标记）。

    Raises:
        ParserError: 内容为空或 PDF 无法解析。
        ScannedPdfError: 没有可提取的文本层（扫描版）。
    """
    if not pdf_bytes:
        raise ParserError("PDF 内容为空")

    pages: list[tuple[int, str]] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = _page_to_text(page).strip()
                pages.append((page.page_number, text))
    except ParserError:
        raise
    except Exception as exc:  # pdfplumber / pdfminer 的各类解析异常
        raise ParserError(f"PDF 解析失败：{exc}") from exc

    if sum(len(text) for _, text in pages) < MIN_TEXT_CHARS:
        raise ScannedPdfError("未提取到文本层，可能是扫描版 PDF；本工具暂不支持 OCR")
    return pages


def _render(pages: list[tuple[int, str]]) -> str:
    blocks = [
        f"{PAGE_MARKER.format(n=number)}\n{_mark_headings(text)}"
        for number, text in pages
    ]
    return "\n\n".join(blocks).strip()


def _page_to_text(page) -> str:
    """单页 → 纯文本：聚行，若为分栏版面则按栏序输出。"""
    words = page.extract_words(
        x_tolerance_ratio=_X_TOLERANCE_RATIO,
        y_tolerance=_Y_TOLERANCE,
        keep_blank_chars=False,
    )
    if not words:
        return ""
    lines = _group_lines(words)
    gutter = _find_gutter(words, page.width, page.height)
    if gutter is None:
        return "\n".join(_line_text(line) for line in lines)
    return "\n".join(_columns_to_text(lines, gutter))


def _group_lines(words: list[dict]) -> list[list[dict]]:
    """按纵坐标把词块聚成行；行内按 x 排序。"""
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for word in ordered:
        if lines and _same_line(lines[-1][-1], word):
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _same_line(a: dict, b: dict) -> bool:
    overlap = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    if overlap <= 0:
        return False
    height = min(a["bottom"] - a["top"], b["bottom"] - b["top"])
    return height > 0 and overlap / height >= _LINE_OVERLAP


def _find_gutter(words: list[dict], width: float, height: float) -> float | None:
    """定位双栏版面的栏缝 x 坐标；单栏返回 None。

    做法：把页面按高度切成水平带，统计每个 x 被多少条带覆盖。两栏之间的
    竖缝在绝大多数带里是空的（仅被横跨整页的标题/页脚覆盖），据此找低谷。
    """
    bands = max(1, math.ceil(height / _BAND_HEIGHT))
    width = int(width)
    coverage = [0] * width
    for word in words:
        left = max(int(word["x0"]), 0)
        right = min(int(word["x1"]), width)
        if right <= left:
            continue
        top_band = max(int(word["top"] // _BAND_HEIGHT), 0)
        bottom_band = min(int(word["bottom"] // _BAND_HEIGHT), bands - 1)
        span = bottom_band - top_band + 1
        for x in range(left, right):
            coverage[x] += span

    lo = int(_GUTTER_SEARCH[0] * width)
    hi = int(_GUTTER_SEARCH[1] * width)
    if hi <= lo:
        return None
    gutter = min(range(lo, hi), key=lambda x: coverage[x])
    if coverage[gutter] > _GUTTER_MAX_COVERAGE * bands:
        return None

    left_words = sum(1 for w in words if w["x1"] <= gutter)
    right_words = sum(1 for w in words if w["x0"] >= gutter)
    floor = _MIN_COLUMN_SHARE * len(words)
    if left_words < floor or right_words < floor:
        return None
    return float(gutter)


def _columns_to_text(lines: list[list[dict]], gutter: float) -> list[str]:
    """按栏缝把行拆到左/右栏；横跨栏缝的行作为整幅行原位输出。"""
    out: list[str] = []
    left: list[str] = []
    right: list[str] = []

    def flush() -> None:
        out.extend(left)
        out.extend(right)
        left.clear()
        right.clear()

    for line in lines:
        left_words = [w for w in line if w["x1"] <= gutter]
        right_words = [w for w in line if w["x0"] >= gutter]
        crossing = [w for w in line if w["x0"] < gutter < w["x1"]]
        if crossing:
            flush()
            out.append(_line_text(line))
        elif left_words and right_words:
            left.append(_line_text(left_words))
            right.append(_line_text(right_words))
        elif left_words:
            left.append(_line_text(left_words))
        else:
            right.append(_line_text(right_words))
    flush()
    return out


def _line_text(line: list[dict]) -> str:
    return " ".join(word["text"] for word in line)


def _mark_headings(text: str) -> str:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and _is_heading(stripped):
            out.append(f"{HEADING_PREFIX}{stripped}")
        else:
            out.append(line)
    return "\n".join(out)


def _is_heading(line: str) -> bool:
    if len(line) > _MAX_HEADING_CHARS:
        return False
    if _KEYWORD_HEADING.match(line):
        return True
    if _NUMBERED_HEADING.match(line) and not line.endswith(_SENTENCE_ENDINGS):
        return _looks_like_title(_LEADING_NUMBER.sub("", line))
    return False


def _looks_like_title(text: str) -> bool:
    """判断编号后的正文是否像标题而非正文列表项。

    正文列表项（如 ``1. Denial of service: make the …``）多为句子式小写从句，
    与 Title Case 的章节标题在大小写上可区分；中文标题无 ASCII 词，直接放行。
    """
    if _URL_LIKE.search(text):
        return False
    if re.search(r"\.\s", text):  # 句中句号 → 是句子而非标题
        return False
    words = re.findall(r"[A-Za-z][A-Za-z\-']*", text)
    if not words:
        return bool(re.search(r"[一-鿿]", text))
    significant = [w for w in words if w.lower() not in _TITLE_STOPWORDS]
    if not significant:
        return False
    upper = sum(1 for w in significant if w[0].isupper())
    return upper * 2 > len(significant)  # 实词多数首字母大写


_ENCODER = None


def _count_tokens(text: str) -> int:
    """用 tiktoken 的 cl100k_base 近似 DeepSeek 的 token 数。"""
    global _ENCODER
    if _ENCODER is None:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return len(_ENCODER.encode(text))


def _char_count(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _print_stats(pages: list[tuple[int, str]]) -> None:
    body = "".join(text for _, text in pages)
    per_page = "  ".join(f"p{n}={_count_tokens(text)}" for n, text in pages)
    print()
    print("──────── 统计 ────────")
    print(f"正文字数（不含空白）：{_char_count(body)}")
    print(f"Token 数（cl100k_base 近似）：{_count_tokens(body)}")
    print(f"每页 Token：{per_page}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("用法：python parser.py <pdf 路径>")
    with open(sys.argv[1], "rb") as fh:
        _pages = _parse_pages(fh.read())
    print(_render(_pages))
    _print_stats(_pages)
