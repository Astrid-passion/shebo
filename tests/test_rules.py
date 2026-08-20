"""规则引擎单元测试：时间解析 / 通知识别 / 事件名匹配"""
import pytest

from app.agent import Agent, parse_time, time_is_valid
from app.llm import RuleBasedBackend


class TestParseTime:
    """中文时间表述 → ISO 时间（NOW 锚点 2026-05-20 周三）"""

    def test_full_datetime(self):
        assert parse_time("6月10日 14:00") == "2026-06-10 14:00"

    def test_no_space(self):
        assert parse_time("6月10日14:00") == "2026-06-10 14:00"

    def test_next_week_fuzzy(self):
        # 下周四 = 2026-05-28，下午3点 = 15:00
        assert parse_time("下周四下午3点") == "2026-05-28 15:00"

    def test_this_week_evening(self):
        # 周五 = 2026-05-22，晚上7点 = 19:00
        assert parse_time("周五晚上7点") == "2026-05-22 19:00"

    def test_weekday_morning(self):
        # 周日 = 2026-05-24，上午9点 = 09:00
        assert parse_time("周日上午9点") == "2026-05-24 09:00"

    def test_noon_and_midnight(self):
        assert parse_time("6月1日中午12点") == "2026-06-01 12:00"
        assert parse_time("6月2日凌晨2点") == "2026-06-02 02:00"

    def test_unparseable(self):
        assert parse_time("时间待定") == ""
        assert parse_time("") == ""


class TestTimeIsValid:
    def test_valid(self):
        assert time_is_valid("2026-06-10 14:00")

    def test_invalid(self):
        assert not time_is_valid("6月10日")
        assert not time_is_valid("")
        assert not time_is_valid("2026-13-01 10:00")


class TestEventSimilarity:
    """事件名匹配：操作词剥离 + 包含关系 + 不相关事件区分"""

    def test_contains_high_sim(self):
        assert Agent._sim("数据结构期末考试", "数据结构期末考试时间变更") == 0.9

    def test_change_word_stripped(self):
        # 「总结会改期」剥离操作词后与「机器人社本学期总结会」足够相似（连续包含不成立，但远超匹配阈值 0.45）
        assert Agent._sim("机器人社本学期总结会", "机器人社总结会改期") >= 0.45

    def test_unrelated_low(self):
        assert Agent._sim("数据结构期末考试", "机器人社总结会") < 0.45


class TestRuleBackend:
    """规则引擎通知识别（无 LLM key 时兜底）"""

    def setup_method(self):
        self.be = RuleBasedBackend()

    def test_exam_notice(self):
        out = self.be.extract(
            "【助教】各位同学，数据结构期末考试定在6月10日 14:00，地点在紫金港校区东1楼101教室，请相互转告")
        assert out["is_notice"] is True
        assert out["event"] == "数据结构期末考试"
        assert out["time_raw"] == "6月10日14:00"
        assert out["location"] == "紫金港校区东1楼101教室"

    def test_summary_notice(self):
        out = self.be.extract(
            "【社长】机器人社本学期总结会将于6月11日 14:00在南校区活动中心201举行，全体成员参加")
        assert out["is_notice"] is True
        assert out["event"] == "机器人社本学期总结会"
        assert out["location"] == "南校区活动中心201"

    def test_change_notice(self):
        out = self.be.extract("【助教】重要通知：数据结构期末考试时间变更为6月12日 14:00，地点不变")
        assert out["is_notice"] is True
        assert out["event"] == "数据结构期末考试"
        assert out["time_raw"] == "6月12日14:00"

    def test_chitchat_filtered(self):
        assert self.be.extract("兄弟们今晚开黑吗？")["is_notice"] is False
        assert self.be.extract("哈哈哈哈哈哈哈")["is_notice"] is False
