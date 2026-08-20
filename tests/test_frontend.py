"""前端完整性测试：关键 UI 元素 / 静态资源引用有效 / JS 语法检查"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"


class TestHtml:
    def test_exists(self):
        assert HTML.exists()

    def test_key_ui_elements(self):
        html = HTML.read_text(encoding="utf-8")
        for marker in ("校园虾宝", "SMART CAMPUS ASSISTANT · SHEBO",
                       "calOverlay", "openCalendar", "subUrl", "subscribe-qr",
                       "确认执行", "日历订阅", "raw_messages"):
            assert marker in html, f"缺少关键元素: {marker}"

    def test_static_assets_exist(self):
        html = HTML.read_text(encoding="utf-8")
        refs = set(re.findall(r'(?:src|href)="(/static/[^"]+)"', html))
        assert refs, "页面未发现 /static/ 资源引用"
        for ref in refs:
            f = ROOT / ref.lstrip("/")
            assert f.exists(), f"静态资源缺失: {ref}"
        assert any("xiabao.png" in r for r in refs), "未引用虾宝 IP 图片"


class TestJsSyntax:
    def test_node_check(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node 不可用，跳过 JS 语法检查")
        html = HTML.read_text(encoding="utf-8")
        m = re.search(r"<script>(.*?)</script>", html, re.S)
        assert m, "未找到内联 <script>"
        tmp = ROOT / ".tmp_check.js"
        tmp.write_text(m.group(1), encoding="utf-8")
        try:
            r = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
            assert r.returncode == 0, f"JS 语法错误:\n{r.stderr}"
        finally:
            tmp.unlink(missing_ok=True)
