"""模块3 编排器：FastAPI HTTP 接口。

职责边界：只做 HTTP 层——请求校验、串联模块1/2、异常兜底、研究方向配置读写。
不做解析/摘要逻辑（分别在 parser / summarizer）。

端点：
    POST /summarize   纯文本 → 7 字段摘要
    POST /upload      PDF 上传 → 7 字段摘要
    GET  /profile     读取默认研究方向
    PUT  /profile     写回默认研究方向（config.yaml）
    GET  /            前端页面（模块4，暂缺时返回提示）

模块间契约不变：``bytes→str``(parser)、``str→dict``(summarizer)；本层只搬运与翻译错误。
摘要为阻塞调用，故端点用同步 ``def``，由 FastAPI 调度到线程池，避免堵住事件循环。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

import parser as parser_module
import summarizer as summarizer_module

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
INDEX_PATH = BASE_DIR / "index.html"

MAX_UPLOAD_MB = 20
_PDF_MAGIC = b"%PDF"

app = FastAPI(title="论文结构化摘要助手", version="0.1.0")


# ── 研究方向配置 ────────────────────────────────────────────────────────

class SummarizeRequest(BaseModel):
    content: str = Field(..., min_length=1)
    research_direction: str | None = None  # 省略则用默认


class Profile(BaseModel):
    research_direction: str = ""


def _load_direction() -> str:
    """启动时加载默认研究方向；config.yaml 不存在时退回空串（该文件不入库）。"""
    if not CONFIG_PATH.exists():
        return ""
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return str(data.get("research_direction", "") or "")


def _save_direction(direction: str) -> None:
    CONFIG_PATH.write_text(
        yaml.safe_dump({"research_direction": direction}, allow_unicode=True),
        encoding="utf-8",
    )


_default_direction = _load_direction()


# ── 异常兜底 ────────────────────────────────────────────────────────────

def _run(action):
    """执行模块调用，把模块异常翻译成 HTTP 状态码。"""
    try:
        return action()
    except parser_module.ParserError as exc:  # 含 ScannedPdfError
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except summarizer_module.SummarizerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── 端点 ────────────────────────────────────────────────────────────────

@app.post("/summarize")
def summarize_text(req: SummarizeRequest) -> dict[str, str]:
    direction = (
        req.research_direction
        if req.research_direction is not None
        else _default_direction
    )
    return _run(lambda: summarizer_module.summarize(req.content, direction))


@app.post("/upload")
def upload(
    file: UploadFile = File(...),
    research_direction: str | None = Form(None),
) -> dict[str, str]:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_MB}MB 上限")
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    direction = (
        research_direction
        if research_direction is not None
        else _default_direction
    )
    return _run(
        lambda: summarizer_module.summarize(parser_module.extract_text(data), direction)
    )


@app.get("/profile")
def get_profile() -> dict[str, str]:
    return {"research_direction": _default_direction}


@app.put("/profile")
def put_profile(profile: Profile) -> dict[str, str]:
    global _default_direction
    _save_direction(profile.research_direction)
    _default_direction = profile.research_direction
    return {"research_direction": _default_direction}


@app.get("/")
def index():
    if INDEX_PATH.exists():
        return FileResponse(INDEX_PATH)
    return HTMLResponse(
        "<h1>论文结构化摘要助手</h1>"
        "<p>前端（模块4）尚未实现。接口文档见 <a href='/docs'>/docs</a>。</p>"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
