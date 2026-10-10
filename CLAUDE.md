# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**本文的模块划分与数据契约是本项目的硬约束：后续所有设计与实现必须遵从。**

## 项目概述

论文结构化摘要工具。输入 PDF 或论文纯文本，调用 DeepSeek 生成 7 项中文结构化摘要，每项 2-3 句：研究问题、研究背景、研究方法、结论、创新点、局限性、对我的价值。「对我的价值」结合用户研究方向个性化生成。

## 技术栈

- Python + FastAPI —— 后端 API
- pdfplumber —— PDF 文本提取
- tiktoken —— 解析器 CLI 统计 token 数（cl100k_base 近似 DeepSeek 分词）
- DeepSeek API —— 摘要生成（JSON mode）
- 单页 HTML —— 前端，无构建步骤

## 架构

数据流：`PDF/纯文本 → 章节切段（Map-Reduce）→ 组装 Prompt（含研究方向）→ DeepSeek → JSON 解析 → 纯文本格式化 → 前端渲染`

### 模块划分

```
论文摘要助手
├── 模块1 解析器 parser.py         职责：PDF → 纯文本（含页码/章节提示）、CLI 打印统计｜不做：OCR
├── 模块2 摘要器 summarizer.py     职责：纯文本 → 结构化摘要 JSON（7 字段）｜不做：文件 IO
├── 模块3 编排器 main.py (FastAPI) 职责：HTTP 接口、模块调用串联、异常兜底｜不做：业务逻辑
└── 模块4 前端 index.html          职责：上传、调接口、渲染结果｜不做：任何计算
```

每个模块「只做一件事」：职责边界即验收边界，越界即为设计缺陷。

### 数据契约

```
PDF 文件 --(bytes)--> parser --(str: 全文文本)--> summarizer --(JSON: 7 字段)--> 前端
```

相邻模块之间只通过上述类型通信，不传递内部实现细节。

扫描版 PDF（无文本层）由 parser 返回明确报错。

parser 的字数/Token 统计只在命令行（`python parser.py <pdf>`）打印，**不进返回值**——契约恒为 `bytes → str`。

## 关键设计决策

### 长论文处理：手工 Map-Reduce

```
按章节切段 → 各段摘要（Map）→ 汇总二次摘要（Reduce）
```

即 RAG 分块思想的预告：先局部压缩，再全局归并，避免超长上下文直接截断导致的信息丢失。

章节切段依据 parser 输出的页码/章节提示，用规则/正则识别章节标题（编号式 `1.`/`1.1`/`第1章`/`一、` 与关键词式 `Abstract/引言/Method/结论` 等，兼容中英文）。纯文本无结构时退化为整篇，并套用长度上限。

7 项摘要与章节的对应关系（用于组织各段与汇总的 prompt）：

| 摘要项 | 主要来源 |
|--------|---------|
| 研究问题 | Abstract + Introduction |
| 研究背景 | Introduction + Related Work |
| 研究方法 | Method / Approach |
| 结论 | Conclusion + Results |
| 创新点 | Abstract + Introduction + Conclusion |
| 局限性 | Discussion + Limitations |
| 对我的价值 | 上述综合 + 研究方向 |

某章节缺失时标记为「缺失」，让 DeepSeek 从已有内容推断。

### 研究方向：config.yaml + 界面可改

config.yaml 存默认研究方向，启动时加载。摘要请求可带 `research_direction` 覆盖默认值。前端输入框预填默认，「保存为默认」写回 config.yaml（`PUT /profile`）。

### 摘要引擎：DeepSeek JSON mode

输出固定 7 字段；JSON 解析失败时降级为纯文本。「中文、每项 2-3 句」的约束写进 prompt。

### 输出格式

本期输出纯文本；预留 `formatter` 接口，后续支持生成 Markdown 文件。

## 数据模型

请求（`POST /summarize` 纯文本 / `POST /upload` PDF）：

```jsonc
{ "content": "论文文本", "research_direction": "可选，覆盖默认" }
```

响应（7 字段固定）：

```jsonc
{
  "research_question": "…",
  "research_background": "…",
  "research_method": "…",
  "conclusion": "…",
  "innovation": "…",
  "limitations": "…",
  "value_to_me": "…"
}
```

研究方向：`GET /profile` 读默认、`PUT /profile` 写回。

## 落地顺序

1. 最小闭环：纯文本 → summarizer → 7 项 JSON（无 PDF、无前端）
2. parser：PDF → 纯文本（含页码/章节提示）+ 章节切段
3. 长文 Map-Reduce：各段摘要 → 汇总二次摘要
4. main.py FastAPI 编排 + index.html 单页前端
5. 研究方向配置 + 输出格式化（md 预留）
