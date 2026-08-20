"""API 层测试：FastAPI TestClient 验证接口、静态资源、完整消息→确认→写日历链路"""
import pytest
from fastapi.testclient import TestClient

import app.agent as agent_mod
from app.llm import RuleBasedBackend
from app.server import app

EXAM = "【助教】各位同学，数据结构期末考试定在6月10日 14:00，地点在紫金港校区东1楼101教室，请相互转告"


@pytest.fixture(autouse=True)
def iso_api(tmp_path, monkeypatch):
    """API 测试隔离：临时数据目录 + 规则引擎后端，不污染真实 data/、不依赖网络"""
    monkeypatch.setattr(agent_mod, "DATA_DIR", tmp_path)
    from app import server
    server.agent.backend = RuleBasedBackend()
    server.agent.reset()
    yield


@pytest.fixture
def client():
    return TestClient(app)


class TestPage:
    def test_home(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "校园虾宝" in r.text
        assert "SMART CAMPUS ASSISTANT · SHEBO" in r.text

    def test_xiabao_png(self, client):
        r = client.get("/static/xiabao.png")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


class TestApi:
    def test_subscribe_qr(self, client):
        r = client.get("/api/subscribe-qr")
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_calendar_ics(self, client):
        r = client.get("/api/calendar.ics")
        assert r.status_code == 200
        assert "BEGIN:VCALENDAR" in r.text

    def test_subscribe_info(self, client):
        r = client.get("/api/subscribe-info")
        assert r.status_code == 200
        body = r.json()
        assert body["webcal_url"].startswith("webcal://")
        assert body["http_url"].endswith("/api/calendar.ics")

    def test_state(self, client):
        r = client.get("/api/state")
        assert r.status_code == 200
        body = r.json()
        for k in ("groups", "llm_backend", "notices", "tasks", "feed", "events"):
            assert k in body
        assert "规则引擎" in body["llm_backend"]

    def test_message_confirm_flow(self, client):
        r = client.post("/api/message", json={"group": "数据结构课程群", "sender": "助教", "text": EXAM})
        assert r.status_code == 200
        body = r.json()
        assert body["recognized"] is True
        tid = body["task"]["id"]

        cr = client.post(f"/api/task/{tid}/confirm", json={"approved": True})
        assert cr.status_code == 200
        assert cr.json()["ok"] is True

        st = client.get("/api/state").json()
        assert len(st["events"]) == 1
        assert st["events"][0]["title"] == "数据结构期末考试"

    def test_reset(self, client):
        assert client.post("/api/reset").status_code == 200
        st = client.get("/api/state").json()
        assert st["events"] == [] and st["notices"] == [] and st["tasks"] == []
