# Cursor 调试参考

本项目在 Cursor（VS Code 内核）中的调试指南。既覆盖本仓库的具体入口，也覆盖通用的断点/单步/排障手法。

---

## 1. 一次性前置设置

两件事没做好，后面全白搭：**解释器**和**环境变量**。

### 1.1 选 Python 解释器

`Ctrl+Shift+P` → `Python: Select Interpreter` → 选本项目的虚拟环境：

```
.venv\Scripts\python.exe        # Python 3.12.9
```

不选的话，Cursor 会用系统 Python，`pdfplumber` / `openai` / `tiktoken` 全部 `ModuleNotFoundError`。

### 1.2 让 Cursor 拿到 `DEEPSEEK_API_KEY`

本项目用环境变量 `DEEPSEEK_API_KEY` 访问 DeepSeek（**不落盘**）。它在 Windows 的 **Machine 作用域**。

**关键坑**：环境变量是进程启动时快照的。**如果 Cursor 是在设置该变量之前启动的，它的集成终端和调试进程都读不到**——这不是代码问题。

**做法**：完全退出 Cursor，从开始菜单重新打开（不要从旧终端里 `cursor .`，那样会继承旧环境）。

进入后在 Cursor 集成终端（`` Ctrl+` ``）验证：

```bash
python -c "import os; print(bool(os.environ.get('DEEPSEEK_API_KEY')))"
```

必须输出 `True`。是 `False` 就说明 Cursor 还是旧环境，关掉重开。

> 备选：用 `envFile`（见 §3）指向一个 `.env`，但会把 key 写到磁盘文件，不推荐，重启更干净。

---

## 2. 本项目的调试入口

| 模块 | 文件 | 输入方式 | 典型用途 |
|------|------|----------|----------|
| 模块1 解析器 | `parser.py` | 命令行参数：PDF 路径 | 调 PDF→文本、章节标记、统计 |
| 模块2 摘要器 | `summarizer.py` | 命令行参数：文本文件（无参则读 stdin） | 调切段、Map-Reduce、JSON 解析 |
| 模块3 编排器 | `main.py`（待建） | uvicorn 服务 | 调 HTTP 接口、端到端 |

### 命令行等价（不依赖 IDE，最快验证）

```bash
# 模块1：PDF → 文本 + 统计
.venv/Scripts/python.exe parser.py "PenForge On-the-Fly Expert Agent Construction for Automated.pdf"

# 模块2：文本文件 → 7 字段 JSON
.venv/Scripts/python.exe summarizer.py sample_paper.txt

# 模块2：从 stdin 读
.venv/Scripts/python.exe summarizer.py < sample_paper.txt
```

`sample_paper.txt` 是由 PDF 转出的调试样本，可随时删除或替换成别的论文文本。

---

## 3. `.vscode/launch.json` 参考

当前内容（两条配置）：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "调试 parser.py（PDF）",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/parser.py",
      "args": ["${workspaceFolder}/PenForge On-the-Fly Expert Agent Construction for Automated.pdf"],
      "console": "integratedTerminal"
    },
    {
      "name": "调试 summarizer.py",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/summarizer.py",
      "args": ["${workspaceFolder}/sample_paper.txt"],
      "console": "integratedTerminal"
    }
  ]
}
```

### 字段说明

| 字段 | 作用 |
|------|------|
| `type` | `debugpy` 是当前 Python 调试器；若插件版本旧报错，改成 `"python"` |
| `program` | 要调试的入口脚本 |
| `args` | 传给脚本的命令行参数（传给 `sys.argv`）；路径含空格要用引号包住 |
| `console` | `integratedTerminal` = 在集成终端跑，**能接收输入**、能看到交互输出 |
| `${workspaceFolder}` | 项目根目录变量，避免写死绝对路径 |

### 常用可选字段

| 字段 | 作用 |
|------|------|
| `"justMyCode": false` | 单步能进第三方库（如 `pdfplumber`、`openai`）内部，排查库行为时用 |
| `"envFile": "${workspaceFolder}/.env"` | 从文件读环境变量（会把 key 落盘，慎用） |
| `"cwd"` | 工作目录，默认 `${workspaceFolder}`，一般不用改 |
| `"stopOnEntry": true` | 启动后立刻在第一行停下 |

### 新增一条配置

`Ctrl+Shift+P` → `Debug: Add Configuration`，或在数组里手写一段。以后 `main.py` 建好后可加：

```json
{
  "name": "调试 FastAPI",
  "type": "debugpy",
  "request": "launch",
  "module": "uvicorn",
  "args": ["main:app", "--reload", "--port", "8000"],
  "console": "integratedTerminal"
}
```

（`module` + `args` 走 uvicorn，与 `program` 二选一。）

---

## 4. 通用调试手法

### 4.1 断点类型

| 类型 | 怎么加 | 用途 |
|------|--------|------|
| 普通断点 | 行号左侧点一下 / `F9` | 执行到该行暂停 |
| 条件断点 | 右键断点 → `Edit Breakpoint` → Expression | 如 `len(text) > 8000`，只在满足时停 |
| 命中计数 | Edit Breakpoint → Hit Count | 如「第 5 次经过时停」，适合循环 |
| 日志断点 (logpoint) | Edit Breakpoint → Log Message | **不暂停**，只打印，如 `text={len(text)}`；想插调试 print 又不想改代码时用 |
| 异常断点 | 运行面板 → `Raise Exceptions` / `User Uncaught Exceptions` | 异常一抛就停，定位报错源头 |

### 4.2 单步控制

| 快捷键 | 动作 |
|--------|------|
| `F5` | 启动 / 继续到下一个断点 |
| `F10` | 单步跳过（Step Over），不进入函数 |
| `F11` | 单步步入（Step Into），进入被调函数 |
| `Shift+F11` | 单步跳出（Step Out），跑完当前函数 |
| `Shift+F5` | 停止调试 |
| `Ctrl+Shift+F5` | 重启调试 |

### 4.3 观察状态

- **变量面板**（左侧）：看当前作用域的局部/全局变量，对象可展开。
- **悬停**：鼠标停在代码里的变量名上，直接看值。
- **监视 (Watch)**：在「监视」面板加表达式，如 `list(out)`、`out["research_method"]`，随单步实时求值。
- **调试控制台 (Debug Console)**：暂停时可直接敲表达式求值，如 `len(text)`、`_count_tokens(text)`、`sections`。等于一个可访问当前作用域的 REPL。

> 本项目的关键观察点：`summarize()` 里的 `text`（输入）、`raw`（模型原始返回）、`out`（归一化结果）；`_parse_json()` 里的 `raw`（看修复逻辑救回了什么）；`_split_sections()` 里的 `title` / `body`。

---

## 5. 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: pdfplumber/openai/tiktoken` | 解释器选错 | `Python: Select Interpreter` 选 `.venv`；或 `pip install -r requirements.txt` |
| 集成终端里 key 读不到（`False`） | Cursor 在设变量之前启动 | 完全退出 Cursor 重开（见 §1.2） |
| F5 后卡住、无输出 | 脚本在读 stdin 而没给输入 | 该用文件参数（本项目 summarizer 已支持 `args`） |
| `DeepSeek 调用失败：... 401` | key 无效或未传入 | 确认 `DEEPSEEK_API_KEY` 已生效（§1.2） |
| `... 404 ... model` | 模型 id 不被 API 接受 | 核对 `summarizer.DEFAULT_MODEL` 是否为有效模型名 |
| 断点是空心灰圈 | 该行不会被命中 / 文件路径不符 | 检查 `program` 路径；确认执行流会到这行 |
| 中文输出乱码 | Windows 控制台默认非 UTF-8 | 终端设 `PYTHONIOENCODING=utf-8`（或在 launch.json 加 `env`） |
| `args` 里路径含空格被拆 | 未加引号 | 参数写成完整一项并在 JSON 里用引号包裹 |
| 想进第三方库却进不去 | 默认 `justMyCode` 为真 | 配置加 `"justMyCode": false` |

---

## 6. 快捷键速查

| 操作 | 快捷键 |
|------|--------|
| 打开运行/调试面板 | `Ctrl+Shift+D` |
| 打开集成终端 | `` Ctrl+` `` |
| 命令面板 | `Ctrl+Shift+P` |
| 设/取消断点 | `F9` |
| 启动/继续 | `F5` |
| 单步跳过 / 步入 / 跳出 | `F10` / `F11` / `Shift+F11` |
| 停止 / 重启调试 | `Shift+F5` / `Ctrl+Shift+F5` |

---

## 7. 一条心法

**先命令行跑通，再上调试器。**

每个脚本都能脱离 IDE 直接跑（§2）。先在集成终端里确认「能跑、输出对」，再启动调试器去断点排障。这样能把「环境/依赖问题」和「逻辑问题」分开——前者在命令行就暴露了，不用在调试器里瞎猜。
