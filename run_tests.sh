#!/usr/bin/env bash
# 校园虾宝 · 上传 GitHub 前一键自测
# 用法: ./run_tests.sh   （输出通过/失败，全部通过后即可推送）
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "❌ 未找到虚拟环境 $PY，请先创建 .venv 并安装依赖（fastapi/uvicorn/pytest/httpx/qrcode）"
  exit 1
fi

echo "========================================"
echo "  校园虾宝 · 自动测试套件"
echo "========================================"

echo
echo "[1/4] 规则引擎单元测试（时间解析 / 通知识别 / 事件匹配）"
PYTHONPATH=. "$PY" -m pytest tests/test_rules.py -q --tb=short

echo
echo "[2/4] Agent 全链路集成测试（新增/去重/改期/冲突/确认链路）"
PYTHONPATH=. "$PY" -m pytest tests/test_agent.py -q --tb=short

echo
echo "[3/4] API 与前端完整性测试（接口/静态资源/JS 语法）"
PYTHONPATH=. "$PY" -m pytest tests/test_api.py tests/test_frontend.py -q --tb=short

echo
echo "[4/4] 汇总跑一遍全部用例"
PYTHONPATH=. "$PY" -m pytest tests/ -q --tb=short

echo
echo "✅ 全部测试通过，可以上传 GitHub 了 🚀"
