"""校园虾宝 - FastAPI 服务入口"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import agent

app = FastAPI(title="校园虾宝 - 群聊 AI Agent")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class Message(BaseModel):
    group: str
    sender: str = "同学A"
    text: str


class Confirm(BaseModel):
    approved: bool


@app.post("/api/message")
def send_message(msg: Message):
    """群消息入口：识别 → 匹配 → 决策 → 生成确认任务"""
    return agent.handle_message(msg.group, msg.sender, msg.text)


@app.get("/api/state")
def state():
    return agent.state()


@app.post("/api/task/{task_id}/confirm")
def confirm(task_id: str, body: Confirm):
    return agent.confirm_task(task_id, body.approved)


@app.post("/api/reset")
def reset():
    agent.reset()
    return {"ok": True}


@app.get("/api/calendar.ics")
def calendar():
    from .agent import DATA_DIR
    return PlainTextResponse((DATA_DIR / "calendar.ics").read_text(encoding="utf-8"),
                             media_type="text/calendar",
                             headers={"Content-Disposition": "attachment; filename=xiaobao-calendar.ics"})


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
