"""Gradio 入口必须只监听本机，并且禁止公网分享。"""
from __future__ import annotations

import ast
from pathlib import Path


APP = Path(__file__).resolve().parent.parent / "app.py"


def _assignments(tree: ast.AST) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            found[target.id] = node.value
    return found


def _launch_call(tree: ast.AST) -> ast.Call:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "launch":
            return node
    raise AssertionError("app.py 里没有 demo.launch")


def test_gradio_launch_stays_on_loopback_without_share() -> None:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    assigned = _assignments(tree)
    server_name = assigned["GRADIO_SERVER_NAME"]
    share = assigned["GRADIO_SHARE"]
    assert isinstance(server_name, ast.Constant) and server_name.value == "127.0.0.1"
    assert isinstance(share, ast.Constant) and share.value is False

    launch = _launch_call(tree)
    keywords = {item.arg: item.value for item in launch.keywords}
    assert isinstance(keywords["server_name"], ast.Name)
    assert keywords["server_name"].id == "GRADIO_SERVER_NAME"
    assert isinstance(keywords["share"], ast.Name)
    assert keywords["share"].id == "GRADIO_SHARE"


def test_yongfeng_upload_shortcut_uses_selection_if_preview_is_stale(monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    import app

    monkeypatch.setattr(app, "_status_html", lambda: "")
    ui = app.build_ui()
    dep = next(item for item in ui.config["dependencies"] if item["api_name"] == "_fill_message")
    fill_command = ui.fns[dep["id"]].fn
    project, business = "永锋钢铁", "检判原图上传下载"
    shortcuts = app._quick_prompts()[project][business]
    stale_preview = shortcuts["下载昨日检判原图"]
    choice = "上传昨日数据集"
    draft, preview = fill_command(stale_preview, project, business, choice)
    assert draft == preview == shortcuts[choice]
