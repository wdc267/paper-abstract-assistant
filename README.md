# 论文结构化摘要助手

输入一篇论文（PDF 或纯文本），调用 DeepSeek 生成 **7 项中文结构化摘要**，每项 2-3 句：研究问题、研究背景、研究方法、结论、创新点、局限性、对我的价值。其中「对我的价值」会结合你的研究方向个性化生成。

## 特性

- **PDF 直接入**：自研版面处理，兼容双栏/多栏学术排版，输出带页码与章节提示的纯文本。
- **长文不丢信息**：超长论文走手工 Map-Reduce——先按章节局部压缩，再全局归并，避免上下文截断。
- **结构化输出**：固定 7 字段，JSON 解析失败自动修复，仍失败则明确报错。
- **个性化**：「对我的价值」依据你的研究方向生成。

## 工作原理

```
PDF ──parser──▶ 纯文本（含页码/章节标记）
                     │
                     ▼
              章节切段（Map-Reduce）
                     │
                     ▼
        组装 Prompt（含研究方向）
                     │
                     ▼
              DeepSeek（JSON mode）
                     │
                     ▼
              JSON 解析 / 修复
                     │
                     ▼
              7 字段结构化摘要
```

全文 token ≤ 8000 时一次调用直接出结果；超过则按章节切段，各段先提炼要点（Map），再汇总二次摘要（Reduce）。

## 模块划分

| 模块 | 文件 | 职责 | 数据契约 |
|------|------|------|----------|
| 模块1 解析器 | `parser.py` | PDF → 纯文本（含页码/章节提示），CLI 打印统计 | `bytes` → `str` |
| 模块2 摘要器 | `summarizer.py` | 纯文本 → 7 字段结构化摘要 | `str` → `dict` |
| 模块3 编排器 | `main.py` | HTTP 接口、模块串联、异常兜底、配置读写 | HTTP 层 |
| 模块4 前端 | `index.html` *(待实现)* | 上传、调接口、渲染结果 | — |

> 模块划分与数据契约是本项目的硬约束，详见 [CLAUDE.md](CLAUDE.md)。

## 环境要求

- Python 3.12+
- 一个可用的 [DeepSeek API Key](https://platform.deepseek.com/)

## 安装

```bash
# 1. 建虚拟环境（如未建）
python -m venv .venv

# 2. 装依赖
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

## 配置

通过环境变量提供 API Key（**不要**写进代码或提交到仓库）：

```
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

Windows 下设为用户/系统环境变量即可。设置后**需重开终端（或 IDE）**，因为已经运行的进程读不到新变量。验证：

```bash
python -c "import os; print(bool(os.environ.get('DEEPSEEK_API_KEY')))"
```

模型默认使用 `deepseek-v4-flash`（在 `summarizer.py` 的 `DEFAULT_MODEL` 中配置）。

## 使用

### 命令行

**模块1：PDF → 文本 + 统计**

```bash
.venv/Scripts/python.exe parser.py "PenForge On-the-Fly Expert Agent Construction for Automated.pdf"
```

打印带 `[第 N 页]` 页码和 `[§]` 章节标记的全文，末尾附字数、Token 数、每页 Token。

**模块2：文本 → 7 字段 JSON**

```bash
# 从文件读
.venv/Scripts/python.exe summarizer.py sample_paper.txt

# 从 stdin 读
.venv/Scripts/python.exe summarizer.py < sample_paper.txt
```

在 Python 中作为库调用：

```python
import parser, summarizer

text = parser.extract_text(open("paper.pdf", "rb").read())
result = summarizer.summarize(text, research_direction="检索增强生成")
print(result["value_to_me"])
```

### HTTP 服务

**启动**（需已配置 `DEEPSEEK_API_KEY`）：

```bash
.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000> 即为前端页面；接口文档见 <http://127.0.0.1:8000/docs>。`config.yaml` 存默认研究方向，**不存在时退回空串**（该文件不入库）。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/summarize` | 纯文本 → 7 字段摘要，body `{"content": "...", "research_direction": "可选"} |
| `POST` | `/upload` | 上传 PDF → 7 字段摘要（仅 PDF，≤20MB），表单字段 `file` 与可选 `research_direction` |
| `GET` | `/profile` | 读取默认研究方向 |
| `PUT` | `/profile` | 写回默认研究方向，body `{"research_direction": "..."}` |
| `GET` | `/` | 前端页面（模块4 未实现时返回占位提示） |

请求省略 `research_direction` 时用默认研究方向；显式传入则覆盖本次。错误映射：解析失败 `400`、模型失败 `502`、参数校验 `422`。

## 输出字段

| 键 | 中文 | 主要来源章节 |
|----|------|--------------|
| `research_question` | 研究问题 | Abstract / Introduction |
| `research_background` | 研究背景 | Introduction / Related Work |
| `research_method` | 研究方法 | Method / Approach |
| `conclusion` | 结论 | Conclusion / Results |
| `innovation` | 创新点 | Abstract / Introduction / Conclusion |
| `limitations` | 局限性 | Discussion / Limitations |
| `value_to_me` | 对我的价值 | 全文综合 + 你的研究方向 |

某章节缺失时标记为「缺失」，由模型从已有内容推断。

## 项目结构

```
paper-abstract-assistant/
├── parser.py            # 模块1 解析器
├── summarizer.py        # 模块2 摘要器
├── main.py              # 模块3 编排器（FastAPI）
├── index.html           # 模块4 前端（待实现）
├── requirements.txt
├── sample_paper.txt     # 调试样本（由测试 PDF 转出）
├── CLAUDE.md            # 项目约束与设计决策
├── DEBUGGING.md         # Cursor 调试指南
├── .vscode/launch.json  # 调试配置
└── .gitignore
```

## 开发与调试

调试方法（断点、单步、环境变量排障等）见 [DEBUGGING.md](DEBUGGING.md)。核心一句：**先用命令行跑通，再上调试器**。

## 当前进度

- [x] 模块1 解析器：PDF → 纯文本，标题识别含 Title Case 校验
- [x] 模块2 摘要器：Map-Reduce + DeepSeek JSON mode
- [x] 模块3 编排器：FastAPI 接口 + config.yaml 研究方向配置
- [ ] 模块4 前端：单页 HTML
- [ ] 输出 Markdown 格式化（预留 `formatter`）
