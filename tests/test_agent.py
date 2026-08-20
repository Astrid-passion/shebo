"""全链路集成测试：新增→确认→写日历 / 跨群去重 / 改期原位更新 / 冲突 / 未确认不写 / 闲聊过滤"""
import pytest

import app.agent as agent_mod
from app.agent import Agent
from app.llm import RuleBasedBackend

G1, G2, G3 = "数据结构课程群", "机器人社社团群", "宿舍群"

EXAM = "【助教】各位同学，数据结构期末考试定在6月10日 14:00，地点在紫金港校区东1楼101教室，请相互转告"
SUMMARY = "【社长】机器人社本学期总结会将于6月11日 14:00在南校区活动中心201举行，全体成员参加"


@pytest.fixture
def ag(tmp_path, monkeypatch):
    """独立 Agent：规则引擎后端 + 临时数据目录，保证确定性且不污染真实 data/"""
    monkeypatch.setattr(agent_mod, "DATA_DIR", tmp_path)
    a = Agent()
    a.backend = RuleBasedBackend()
    return a


class TestAddFlow:
    def test_new_notice_creates_pending_task(self, ag):
        r = ag.handle_message(G1, "助教", EXAM)
        assert r["recognized"] is True
        assert r["task"]["type"] == "add"
        assert r["task"]["action"] == "pending"
        assert r["notice"]["event"] == "数据结构期末考试"
        assert r["notice"]["time"] == "2026-06-10 14:00"
        assert r["notice"]["location"] == "紫金港校区东1楼101教室"
        # 确认卡片附消息原文
        assert r["task"]["detail"]["raw_messages"][0]["text"] == EXAM

    def test_confirm_writes_ics(self, ag):
        r = ag.handle_message(G1, "助教", EXAM)
        tid = r["task"]["id"]
        assert ag._read_events() == []          # 确认前不写日历
        cr = ag.confirm_task(tid, True)
        assert cr["ok"] is True
        events = ag._read_events()
        assert len(events) == 1
        assert events[0]["title"] == "数据结构期末考试"
        assert events[0]["start"] == "2026-06-10 14:00"
        assert events[0]["location"] == "紫金港校区东1楼101教室"

    def test_dismiss_does_not_write(self, ag):
        r = ag.handle_message(G1, "助教", EXAM)
        cr = ag.confirm_task(r["task"]["id"], False)
        assert cr["ok"] is True
        assert ag._read_events() == []
        assert next(t for t in ag.tasks if t.id == r["task"]["id"]).action == "dismissed"

    def test_second_confirm_rejected(self, ag):
        r = ag.handle_message(G1, "助教", EXAM)
        ag.confirm_task(r["task"]["id"], True)
        assert ag.confirm_task(r["task"]["id"], True)["ok"] is False   # 已处理，不可重复确认


class TestDedupFlow:
    def test_cross_group_dedup(self, ag):
        r1 = ag.handle_message(G1, "助教", EXAM)
        ag.confirm_task(r1["task"]["id"], True)
        r2 = ag.handle_message(G3, "同学",
                               "【同学】提醒一下，数据结构期末考试6月10日 14:00在东1楼101，大家考试周别忘了复习")
        assert r2.get("dedup") is True
        assert len(ag._read_events()) == 1      # 不产生新事件

    def test_dedup_merges_into_pending_card(self, ag):
        r1 = ag.handle_message(G1, "助教", EXAM)
        r2 = ag.handle_message(G3, "同学",
                               "【同学】提醒一下，数据结构期末考试6月10日 14:00在东1楼101，大家考试周别忘了复习")
        # 原任务仍在待确认，新消息应并入同一张卡片
        pending = next(t for t in ag.tasks if t.id == r1["task"]["id"])
        assert len(pending.detail["raw_messages"]) == 2


class TestUpdateFlow:
    def test_change_updates_in_place(self, ag):
        r1 = ag.handle_message(G1, "助教", EXAM)
        ag.confirm_task(r1["task"]["id"], True)
        uid = ag._read_events()[0]["uid"]

        r2 = ag.handle_message(G1, "助教",
                               "【助教】重要通知：数据结构期末考试时间变更为6月12日 14:00，地点不变")
        assert r2["task"]["type"] == "update"
        assert r2["task"]["summary"] == "2026-06-10 14:00 → 2026-06-12 14:00（来自 数据结构课程群）"
        ag.confirm_task(r2["task"]["id"], True)

        events = ag._read_events()
        assert len(events) == 1                 # 原位改写而非新增
        assert events[0]["uid"] == uid          # UID 不变
        assert events[0]["start"] == "2026-06-12 14:00"
        assert "已改期" in events[0]["title"]

    def test_change_after_dismiss_degrades_to_new(self, ag):
        """原事件未入册（曾被拒绝），改期降级为新建并标注"""
        r1 = ag.handle_message(G1, "助教", EXAM)
        ag.confirm_task(r1["task"]["id"], False)    # 拒绝，不写日历
        r2 = ag.handle_message(G1, "助教",
                               "【助教】重要通知：数据结构期末考试时间变更为6月12日 14:00，地点不变")
        ag.confirm_task(r2["task"]["id"], True)
        events = ag._read_events()
        assert len(events) == 1
        assert events[0]["start"] == "2026-06-12 14:00"


class TestConflictFlow:
    def test_cross_group_conflict_high_risk(self, ag):
        ag.handle_message(G1, "助教", EXAM)
        r = ag.handle_message(G2, "社长",
                              "【社长】重要提醒（转自教务）：数据结构期末考试时间为6月11日 9:00，"
                              "与此前课程群说的6月10日 14:00不一致，请同学们以教务答复为准")
        assert r["task"]["type"] == "conflict"
        assert r["task"]["risk"] == "high"
        assert "2026-06-10 14:00" in r["task"]["summary"] and "2026-06-11 09:00" in r["task"]["summary"]


class TestChitchatFlow:
    def test_chitchat_filtered(self, ag):
        r = ag.handle_message(G3, "我", "兄弟们今晚开黑吗？")
        assert r["recognized"] is False
        assert ag.tasks == []
