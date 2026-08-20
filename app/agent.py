"""校园虾宝 - Agent 核心逻辑

链路：群消息 → 通知识别（LLM/规则引擎）→ 跨群去重与冲突检测 → 任务决策 → 用户确认 → Tool Calling 写日历(.ics)
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from .llm import get_backend

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------- 数据模型
@dataclass
class Notice:
    """一条结构化通知（可能来自多个群的多个消息）"""
    id: str
    event: str                 # 事件名，如「数据结构期末考试」
    time: str                  # 事件时间，如 2026-06-10 14:00（无法解析时为原始文本）
    time_raw: str              # 原始时间表述
    location: str
    source_groups: list = field(default_factory=list)   # 出现过的群
    raw_messages: list = field(default_factory=list)    # 原始消息记录 {group, sender, text, ts}
    version: int = 1
    status: str = "active"     # active / updated / conflict
    calendar_uid: str = ""     # 关联的日历事件 UID

    def to_dict(self):
        return asdict(self)


@dataclass
class Task:
    """Agent 产生的待决策任务（Human-in-the-loop 确认卡片）"""
    id: str
    type: str                  # add / update / conflict / remind
    notice_id: str
    title: str
    summary: str
    detail: dict = field(default_factory=dict)
    risk: str = "low"          # low / medium / high
    action: str = "pending"    # pending / confirmed / dismissed
    auto: bool = False         # 是否低风险自动执行（写日历仍需可见）
    tool_call: dict = field(default_factory=dict)  # 预生成的 Tool Calling 请求
    result: str = ""
    created_at: str = ""

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- Tool 定义（与 PRD 对齐）
TOOLS_SPEC = [
    {
        "name": "create_calendar_event",
        "description": "在用户日历中创建一个新事件。适用于全新通知，且用户已确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "事件标题"},
                "start": {"type": "string", "description": "开始时间 ISO 格式 YYYY-MM-DD HH:MM"},
                "duration_min": {"type": "integer", "description": "时长（分钟），默认 60"},
                "location": {"type": "string"},
                "description": {"type": "string", "description": "来源群与原文摘要"},
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "update_calendar_event",
        "description": "更新已存在的日历事件（如考试时间变更）。仅在用户确认后调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "原事件 UID"},
                "new_start": {"type": "string"},
                "note": {"type": "string", "description": "变更说明"},
            },
            "required": ["uid", "new_start"],
        },
    },
]


# ---------------------------------------------------------------- 时间解析（规则引擎兜底）
NOW = datetime(2026, 5, 20)  # demo 时间锚点，贴近创造营项目周期

MONTH_DAY = re.compile(r"(\d{1,2})月(\d{1,2})日")
CLOCK = re.compile(r"(上午|中午|下午|晚上|凌晨)?(\d{1,2})[:：点](\d{2})?")
WEEKDAY = re.compile(r"(下周|本周|这周|周)([一二三四五六日天])")
WD_IDX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def parse_time(text: str) -> str:
    """把中文时间表述解析为 ISO 时间（支持 X月X日 / 周X / 下周X + 上午下午晚上），失败返回空串"""
    md = MONTH_DAY.search(text)
    clock = CLOCK.search(text)
    if not md:
        wm = WEEKDAY.search(text)
        if not wm:
            return ""
        target = WD_IDX[wm.group(2)]
        delta = (target - NOW.weekday()) % 7
        if wm.group(1) == "下周":
            delta += 7
        elif delta == 0 and clock is None:
            return ""
        base = NOW + timedelta(days=delta)
        month, day = base.month, base.day
    else:
        month, day = int(md.group(1)), int(md.group(2))
    if clock:
        hh = int(clock.group(2))
        mm = int(clock.group(3)) if clock.group(3) else 0
        ap = clock.group(1)
        if ap in ("下午", "晚上") and hh < 12:
            hh += 12
        elif ap == "中午" and hh < 12:
            hh = 12
        elif ap == "凌晨" and hh == 12:
            hh = 0
    else:
        hh, mm = 9, 0
    year = NOW.year if (month, day) >= (NOW.month, NOW.day) else NOW.year + 1
    try:
        return datetime(year, month, day, hh, mm).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def time_is_valid(t: str) -> bool:
    try:
        datetime.strptime(t, "%Y-%m-%d %H:%M")
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- Agent 主体
class Agent:
    def __init__(self):
        self.backend = get_backend()
        self.notices: list[Notice] = []
        self.tasks: list[Task] = []
        self.feed: list[dict] = []          # Agent 思考过程日志（演示用）
        self.groups = ["数据结构课程群", "机器人社社团群", "宿舍群"]
        self.reset()

    # ------------------------------------------------ 公共接口
    def reset(self):
        self.notices, self.tasks, self.feed = [], [], []
        DATA_DIR.mkdir(exist_ok=True)
        (DATA_DIR / "calendar.ics").write_text(self._ics_header(), encoding="utf-8")
        self.log("system", "校园虾宝 Agent 已启动", "多群通知监听中：数据结构课程群 / 机器人社社团群 / 宿舍群")

    def handle_message(self, group: str, sender: str, text: str) -> dict:
        """处理一条群消息：识别 → 匹配 → 决策 → 生成任务"""
        self.log("listen", f"[{group}] {sender}", text)

        # 1. 通知识别（LLM 优先，规则引擎兜底）
        parsed = self.backend.extract(text)
        if not parsed or not parsed.get("is_notice"):
            self.log("skip", "识别结果：闲聊/非通知", "不生成任务，继续监听")
            return {"recognized": False}

        t_norm = parsed.get("time", "")
        notice = Notice(
            id=uuid.uuid4().hex[:8],
            event=parsed.get("event", "").strip(),
            time=t_norm if time_is_valid(t_norm) else parse_time(parsed.get("time_raw", "")),
            time_raw=parsed.get("time_raw", ""),
            location=parsed.get("location", ""),
            source_groups=[group],
            raw_messages=[{"group": group, "sender": sender, "text": text,
                           "ts": datetime.now().strftime("%H:%M:%S")}],
        )
        self.log("extract", "通知识别",
                 f"事件「{notice.event}」 时间「{notice.time or notice.time_raw}」 地点「{notice.location or '未提取'}」")

        # 2. 跨群匹配：判重 / 变更 / 冲突
        matched = self._match(notice)
        if matched is None:
            decision = "新增通知"
            self.notices.append(notice)
            task = self._make_task(notice, "add")
        else:
            same_event, time_changed, cross_group = matched
            notice.id = same_event.id
            # 记录新来源消息，便于追溯信息链路
            same_event.raw_messages.append(notice.raw_messages[0])
            if time_changed and cross_group:
                decision = "跨群时间冲突"
                same_event.status = "conflict"
                same_event.source_groups.append(group)
                task = self._make_task(same_event, "conflict", other=notice)
            elif time_changed:
                decision = "通知变更"
                same_event.status = "updated"
                same_event.version += 1
                task = self._make_task(same_event, "update", other=notice)
            else:
                decision = "重复通知（已跨群去重）"
                same_event.source_groups.append(group)
                # 若该事件还有待确认卡片，把新消息汇总进去（人工确认时可见全部原文）
                pending = next((t for t in self.tasks
                                if t.notice_id == same_event.id and t.action == "pending"), None)
                if pending:
                    pending.detail["raw_messages"] = [dict(m) for m in same_event.raw_messages]
                    self.log("dedup", "重复通知已汇总进待确认卡片",
                             f"「{same_event.event}」累计 {len(same_event.raw_messages)} 条消息 / "
                             f"{len(set(same_event.source_groups))} 个群，等待人工确认")
                else:
                    self.log("dedup", decision, f"与已有通知「{same_event.event}」判定为同一事件，不重复提醒")
                return {"recognized": True, "dedup": True, "notice": same_event.to_dict()}

        self.log("decide", f"任务决策：{decision}",
                 f"生成确认卡片 → {task.title}（风险等级 {task.risk}）")
        self.tasks.append(task)
        return {"recognized": True, "notice": notice.to_dict(), "task": task.to_dict()}

    def confirm_task(self, task_id: str, approved: bool) -> dict:
        """用户确认/拒绝 → 真正执行 Tool Calling"""
        task = next((t for t in self.tasks if t.id == task_id), None)
        if not task or task.action != "pending":
            return {"ok": False, "msg": "任务不存在或已处理"}
        if not approved:
            task.action = "dismissed"
            task.result = "用户已拒绝，未执行任何工具调用"
            self.log("human", "用户拒绝", f"「{task.title}」已取消，日历未改动")
            return {"ok": True, "task": task.to_dict()}

        # Human-in-the-loop：确认后才执行 Tool Call
        task.action = "confirmed"
        result = self._execute_tool(task)
        task.result = result
        self.log("tool", f"Tool Calling：{task.tool_call.get('name')}",
                 f"执行成功 → {result}")
        return {"ok": True, "task": task.to_dict(), "result": result}

    def state(self) -> dict:
        return {
            "groups": self.groups,
            "llm_backend": self.backend.name,
            "notices": [n.to_dict() for n in self.notices],
            "tasks": [t.to_dict() for t in reversed(self.tasks)],
            "feed": list(reversed(self.feed[-60:])),
            "events": self._read_events(),
        }

    # ------------------------------------------------ 内部：匹配与决策
    CHANGE_WORDS = re.compile(r"(改期|变更|推迟|提前|调整|时间|通知|改到|改至)")

    @classmethod
    def _sim(cls, a: str, b: str) -> float:
        """事件名相似度：先去掉变更操作词，再做包含/序列比对"""
        na = cls.CHANGE_WORDS.sub("", a).strip()
        nb = cls.CHANGE_WORDS.sub("", b).strip()
        if len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na):
            return 0.9
        return SequenceMatcher(None, a, b).ratio()

    def _match(self, new: Notice):
        """返回 (matched_notice, time_changed, cross_group) 或 None"""
        for old in self.notices:
            sim = self._sim(old.event, new.event)
            if sim < 0.45:
                continue
            cross = new.source_groups[0] not in old.source_groups
            if new.time and old.time:
                # 双方时间都可解析：不同即变更/冲突
                return old, new.time != old.time, cross
            if sim > 0.8:
                return old, False, cross
        return None

    def _make_task(self, notice: Notice, ttype: str, other: Notice | None = None) -> Task:
        if ttype == "add":
            title = f"新增日程：{notice.event}"
            summary = f"{notice.time or notice.time_raw} · {notice.location or '地点待定'}（来自 {notice.source_groups[0]}）"
            tool = {"name": "create_calendar_event",
                    "arguments": {"title": notice.event,
                                  "start": notice.time,
                                  "duration_min": 120,
                                  "location": notice.location,
                                  "description": f"来源：{notice.source_groups[0]}"}}
            risk = "medium"
        elif ttype == "update":
            new_time = other.time or other.time_raw
            title = f"时间变更：{notice.event}"
            summary = f"{notice.time or notice.time_raw} → {new_time}（来自 {other.source_groups[0]}）"
            tool = {"name": "update_calendar_event",
                    "arguments": {"uid": notice.calendar_uid or notice.id,
                                  "new_start": other.time,
                                  "note": f"{other.source_groups[0]} 通知时间变更"}}
            risk = "medium"
        else:  # conflict
            title = f"⚠️ 跨群冲突：{notice.event}"
            summary = (f"{notice.source_groups[0]}：{notice.time or notice.time_raw} vs "
                       f"{other.source_groups[0]}：{other.time or other.time_raw}")
            tool = {"name": "create_calendar_event",
                    "arguments": {"title": f"[需人工核实] {notice.event}",
                                  "start": notice.time,
                                  "duration_min": 120,
                                  "location": notice.location,
                                  "description": f"冲突信息：{summary}"}}
            risk = "high"
        # 汇总消息原文：本通知历史消息 + 触发本次决策的新消息（人工确认前可追溯全部信息）
        msgs = [dict(m) for m in notice.raw_messages]
        if other is not None:
            msgs += [dict(m) for m in other.raw_messages]
        return Task(id=uuid.uuid4().hex[:8], type=ttype, notice_id=notice.id,
                    title=title, summary=summary, risk=risk, tool_call=tool,
                    detail={"raw_messages": msgs},
                    created_at=datetime.now().strftime("%H:%M:%S"))

    # ------------------------------------------------ 内部：工具执行（写 .ics）
    def _execute_tool(self, task: Task) -> str:
        name = task.tool_call.get("name")
        args = task.tool_call.get("arguments", {})
        ics_file = DATA_DIR / "calendar.ics"
        content = ics_file.read_text(encoding="utf-8")
        stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
        if name == "create_calendar_event":
            uid = uuid.uuid4().hex[:12]
            start = args.get("start", "")
            if time_is_valid(start):
                dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
                dur = int(args.get("duration_min", 60))
                end = dt + timedelta(minutes=dur)
                s, e = dt.strftime("%Y%m%dT%H%M%S"), end.strftime("%Y%m%dT%H%M%S")
            else:
                dt = NOW + timedelta(days=7)
                s = dt.strftime("%Y%m%dT%H%M%S")
                e = (dt + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")
            content += (f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{stamp}\nDTSTART:{s}\nDTEND:{e}\n"
                        f"SUMMARY:{args.get('title','')}\nLOCATION:{args.get('location','')}\n"
                        f"DESCRIPTION:{args.get('description','')}\nEND:VEVENT\n")
            ics_file.write_text(content, encoding="utf-8")
            notice = next((n for n in self.notices if n.id == task.notice_id), None)
            if notice:
                notice.calendar_uid = uid
            return f"已创建日历事件（UID {uid}），写入 calendar.ics"
        if name == "update_calendar_event":
            uid = args.get("uid")
            new_start = args.get("new_start", "")
            if not time_is_valid(new_start):
                return "新时间无法解析，未更新日历"
            dt = datetime.strptime(new_start, "%Y-%m-%d %H:%M")
            s = dt.strftime("%Y%m%dT%H%M%S")
            e = (dt + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")
            # 真实更新：找到原事件，改写其 DTSTART/DTEND，而不是追加新事件
            blocks = re.findall(r"BEGIN:VEVENT\n.*?END:VEVENT\n", content, re.S)
            head = content.split("BEGIN:VEVENT", 1)[0]
            updated = False
            for i, blk in enumerate(blocks):
                if f"UID:{uid}" in blk:
                    nb = re.sub(r"DTSTART:\d{8}T\d{6}", f"DTSTART:{s}", blk)
                    nb = re.sub(r"DTEND:\d{8}T\d{6}", f"DTEND:{e}", nb)
                    if "已改期" not in nb:
                        nb = re.sub(r"SUMMARY:(.+)", lambda m: f"SUMMARY:{m.group(1)}（已改期）", nb, count=1)
                    nb = nb.replace("END:VEVENT", f"X-LAST-UPDATED:{stamp}\nEND:VEVENT")
                    blocks[i] = nb
                    updated = True
                    break
            if updated:
                ics_file.write_text(head + "".join(blocks), encoding="utf-8")
                # 同步内存通知：后续去重/变更检测以新时间为准
                notice = next((n for n in self.notices if n.id == task.notice_id), None)
                if notice:
                    notice.time = new_start
                    notice.status = "active"
                return f"日历事件已更新（UID {uid} → {new_start}，原事件被改写而非新建）"
            # 原事件未入册（如新增任务被忽略过），降级为新建并标注
            content += (f"BEGIN:VEVENT\nUID:{uid}-v2\nDTSTAMP:{stamp}\nDTSTART:{s}\nDTEND:{e}\n"
                        f"SUMMARY:{args.get('note', '变更') or '变更'}（已改期）\nEND:VEVENT\n")
            ics_file.write_text(content, encoding="utf-8")
            return f"原事件未找到，已新建改期事件（{new_start}），写入 calendar.ics"
        return "未知工具，未执行"

    def _read_events(self) -> list[dict]:
        events, cur = [], None
        for line in (DATA_DIR / "calendar.ics").read_text(encoding="utf-8").splitlines():
            if line.startswith("BEGIN:VEVENT"):
                cur = {}
            elif line.startswith("END:VEVENT"):
                if cur:
                    events.append(cur)
                cur = None
            elif cur is not None and ":" in line:
                k, v = line.split(":", 1)
                cur[k] = v
        out = []
        for ev in events:
            try:
                out.append({"uid": ev.get("UID"), "title": ev.get("SUMMARY", ""),
                            "start": datetime.strptime(ev["DTSTART"], "%Y%m%dT%H%M%S").strftime("%Y-%m-%d %H:%M"),
                            "location": ev.get("LOCATION", "")})
            except (KeyError, ValueError):
                continue
        return out

    @staticmethod
    def _ics_header() -> str:
        return ("BEGIN:VCALENDAR\nVERSION:2.0\n"
                "PRODID:-//Xiaobao//Campus Group Agent//CN\n"
                "CALSCALE:GREGORIAN\n")

    def log(self, phase: str, title: str, detail: str = ""):
        self.feed.append({"phase": phase, "title": title, "detail": detail,
                          "ts": datetime.now().strftime("%H:%M:%S")})


# 全局单例
agent = Agent()
