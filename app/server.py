"""校园虾宝 - FastAPI 服务入口"""
from __future__ import annotations

import io
import socket
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import agent

app = FastAPI(title="校园虾宝 - 群聊 AI Agent")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PORT = 8765


def lan_ip() -> str:
    """获取本机局域网 IP（供手机订阅日历使用）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


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


@app.get("/api/subscribe-info")
def subscribe_info():
    """返回日历订阅链接（手机 / 系统日历订阅用）"""
    ip = lan_ip()
    return {
        "lan_ip": ip,
        "http_url": f"http://{ip}:{PORT}/api/calendar.ics",
        "webcal_url": f"webcal://{ip}:{PORT}/api/calendar.ics",
    }


@app.get("/api/subscribe-qr")
def subscribe_qr():
    """生成订阅日历的二维码（手机扫码即可订阅）"""
    import qrcode
    url = subscribe_info()["webcal_url"]
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
