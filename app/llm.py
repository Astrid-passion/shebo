"""LLM 接入层：可插拔后端

- RuleBasedBackend：规则引擎兜底（默认，无需 API key，开箱即用）
- OpenAICompatBackend：填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 环境变量后自动启用，
  走真实大模型（OpenAI 兼容协议，DeepSeek/智谱/通义/混元均可）
"""
from __future__ import annotations

import json
import os
import re
import urllib.request


class RuleBasedBackend:
    """规则引擎：关键词 + 正则做通知识别，保证无 key 也能完整跑通链路"""
    name = "规则引擎（未配置 API key）"

    NOTICE_KEYWORDS = re.compile(
        r"(考试|讲座|答辩|截止|提交|开会|活动|比赛|报名|面试|上课|调课|停课|交作业|实验|打卡|签到|展览|宣讲)")
    EVENT_STOPWORDS = "的通知|通知|请注意|请及时|各位同学|同学们|大家"

    def extract(self, text: str) -> dict:
        if not self.NOTICE_KEYWORDS.search(text):
            return {"is_notice": False}
        # 提取事件名：通知式表述 → 关键词式表述 → 兜底截断
        event = ""
        m = re.search(r"关于?(.{2,20}?)(?:的)?(?:通知|安排)", text)
        if m:
            event = m.group(1)
        if not event:
            m = re.search(r"([\u4e00-\u9fa5]{2,14})(?:期末|期中)?(?:考试|讲座|答辩|比赛|会议|总结会|活动|实验|检查|报名)", text)
            if m:
                event = m.group(0)
        if not event:
            event = text[:16]
        for w in ("将于", "定在", "定于", "将在", "时间为", "时间是", "时间改为", "改至", "改到", "变更为"):
            if w in event:
                event = event.split(w)[0]
        event = re.sub(self.EVENT_STOPWORDS, "", event).strip() or text[:16]
        time_raw = ""
        tm = re.search(r"(\d{1,2}月\d{1,2}日(?:\s*\d{1,2}[:：]\d{2})?|周[一二三四五六日天](?:\s*\d{1,2}[:：]\d{2})?)", text)
        if tm:
            time_raw = tm.group(1).replace(" ", "")
        loc = ""
        lm = re.search(r"(?:在|地点[::])([\u4e00-\u9fa5A-Za-z0-9]{2,12}(?:楼|室|教室|厅|场|馆|中心))", text)
        if lm:
            loc = lm.group(1)
        return {"is_notice": True, "event": event, "time_raw": time_raw, "location": loc}


class OpenAICompatBackend:
    """真实 LLM 后端：OpenAI 兼容 /chat/completions，结构化抽取"""
    name = ""

    def __init__(self):
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")
        self.name = f"LLM：{self.model}"

    def extract(self, text: str) -> dict:
        prompt = (
            "你是校园群聊通知提取器。当前日期：2026年5月20日（周三）。\n"
            "判断这条大学生群消息是否是一条【正式通知】（考试/讲座/截止日期/活动/会议/改期变更等）。\n"
            "如果是，提取：\n"
            "- event: 事件本名，如「机器人社总结会」「数据结构期末考试」；"
            "不要包含「改期/变更/推迟/调整/通知」等操作词\n"
            "- time_raw: 原文中的时间表述\n"
            "- time: 把时间解析为 YYYY-MM-DD HH:MM 格式；相对表述（如「下周三下午3点」「周五晚上7点」）"
            "必须以当前日期为基准换算成具体日期时间；解析不了给空串\n"
            "- location: 地点，没有则空串\n"
            "只输出 JSON：{\"is_notice\":bool,\"event\":str,\"time_raw\":str,\"time\":str,\"location\":str}\n"
            f"消息：{text}"
        )
        try:
            body = json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }).encode()
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            out = json.loads(content)
            out["is_notice"] = bool(out.get("is_notice"))
            return out
        except Exception as e:  # LLM 失败 → 回退规则引擎
            out = RuleBasedBackend().extract(text)
            out["_fallback"] = f"LLM 调用失败({e})，已回退规则引擎"
            return out


def get_backend():
    if os.environ.get("LLM_API_KEY"):
        return OpenAICompatBackend()
    return RuleBasedBackend()
