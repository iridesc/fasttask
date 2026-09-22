#!/usr/bin/env python3
"""审计存量 FastTask 项目的 MCP 元信息，列出还需要补什么。

为什么需要它
------------
MCP 给 AI 看的信息只有两处来源：

- ``instructions``（全局一份）：来自 ``setting.py`` 的 title/summary/description/version，
  加上框架固定的调用约定与「任务一览」（每个任务取模块 docstring 的首段）
- ``tools[].description``（每个工具一份）：来自任务模块的 docstring

所以一个没有 docstring 的任务，在 AI 眼里就只有一个函数名；``setting.py`` 空着，
instructions 里就没有模块定位。两者一起决定模型会不会选对工具、填对参数。

本脚本用 AST 静态解析（**不 import 项目代码**，避免触发副作用与依赖缺失），
把「还缺什么」一次性列出来，供补写元信息时逐项销账。

用法
----
    python3 audit_mcp_metadata.py <项目目录>            # 人读的报告
    python3 audit_mcp_metadata.py <项目目录> --json     # 机器读（CI/脚本）

退出码：0 = 没有待办；1 = 有待办（方便在 CI 或循环里当门禁用）。
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

#: project_summary 直接排在 MCP instructions 最前面，而客户端普遍只展示前几百字符；
#: 框架在超过 100 字符时会启动告警（见 utils/mcp_server.py 的 _SUMMARY_MAX_LENGTH）。
SUMMARY_MAX_LENGTH = 100

#: 任务一览里每个任务占一行，首段太长会把一览读成一坨，失去「一眼扫完」的作用。
FIRST_PARAGRAPH_MAX_LENGTH = 120

SETTING_FIELDS = (
    "project_title",
    "project_summary",
    "project_description",
    "project_version",
)

#: 任务的输入/输出模型；只有这两个会被框架拿去做 API/MCP 的 schema 与参数说明。
MODEL_NAMES = ("Params", "Result")


def literal_str(node):
    """把 AST 节点还原成字符串（字符串常量、隐式拼接、``+`` 拼接、f-string 占位）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string：拼不出确定值，返回占位
        return "<f-string>"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_str(node.left), literal_str(node.right)
        if left is not None or right is not None:
            return (left or "") + (right or "")
    return None


def parse(path):
    """解析 Python 文件；语法错误时返回 None（老项目里可能有 py2 风格文件）。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def first_paragraph(text):
    """取首段（遇到空行即停）——框架正是用它生成 instructions 里的任务一览。"""
    lines = []
    for line in (text or "").splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)


def audit_setting(project_dir):
    """检查 setting.py 的四个字段是否都在、summary 有没有过长。"""
    path = project_dir / "setting.py"
    result = {
        "exists": path.is_file(),
        "values": {name: None for name in SETTING_FIELDS},
        "missing": [],
    }
    if not result["exists"]:
        result["missing"] = list(SETTING_FIELDS)
        return result

    tree = parse(path)
    if tree is None:
        result["missing"] = list(SETTING_FIELDS)
        result["error"] = "setting.py 无法解析"
        return result

    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in SETTING_FIELDS:
            continue
        value = literal_str(node.value)
        result["values"][target.id] = value

    result["missing"] = [
        name for name in SETTING_FIELDS if not (result["values"].get(name) or "").strip()
    ]
    return result


def field_description(node):
    """AnnAssign 的值是否用了 Field(description=...)；返回 (有无描述, 是否 Field 赋值)。"""
    value = node.value
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "Field":
            return any(kw.arg == "description" for kw in value.keywords), True
    return False, False


def audit_models(tree):
    """检查 Params / Result 的字段是否都写了 description。"""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in MODEL_NAMES:
            continue
        fields = []
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            has_desc, is_field = field_description(item)
            try:
                annotation = ast.unparse(item.annotation)
            except Exception:  # noqa: BLE001 - 极端语法下不阻塞审计
                annotation = "?"
            fields.append(
                {
                    "name": item.target.id,
                    "type": annotation,
                    "has_description": has_desc,
                    "uses_field": is_field,
                }
            )
        out[node.name] = fields
    return out


def audit_task(path):
    """审计单个任务模块。"""
    tree = parse(path)
    info = {
        "task": path.stem,
        "file": str(path),
        "parse_error": tree is None,
        "docstring": None,
        "docstring_first_paragraph": "",
        "docstring_body": "",
        "docstring_length": 0,
        "models": {},
        "missing_field_descriptions": [],
    }
    if tree is None:
        return info

    doc = ast.get_docstring(tree, clean=False)
    if doc:
        doc = doc.strip()
        first = first_paragraph(doc)
        info["docstring"] = doc
        info["docstring_first_paragraph"] = first
        info["docstring_length"] = len(doc)
        # 用原文按空行切，而不是拿规范化后的首段去切片：首段换行折叠后长度会变，
        # 切片会错位，导致“只有首段”的提醒误报。
        parts = re.split(r"\n\s*\n", doc, maxsplit=1)
        info["docstring_body"] = parts[1].strip() if len(parts) > 1 else ""

    info["models"] = audit_models(tree)
    for model_name in MODEL_NAMES:
        for field in info["models"].get(model_name, []):
            if not field["has_description"]:
                info["missing_field_descriptions"].append(f"{model_name}.{field['name']}")
    return info


def collect(project_dir):
    tasks_dir = project_dir / "tasks"
    tasks = []
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.py")):
            if path.name.startswith("__"):
                # tasks/__init__.py 会被框架当成任务模块（本身就不该存在），不审计
                continue
            tasks.append(audit_task(path))
    return tasks


def build_report(project_dir):
    setting = audit_setting(project_dir)
    tasks = collect(project_dir)
    todo = []
    warnings = []

    if not setting["exists"]:
        todo.append("setting.py 不存在：MCP instructions 里将没有任何模块定位信息")
    else:
        for name in setting["missing"]:
            todo.append(f"setting.py 缺 {name}")
        summary = setting["values"].get("project_summary") or ""
        if len(summary) > SUMMARY_MAX_LENGTH:
            warnings.append(
                f"project_summary 长度 {len(summary)} 超过 {SUMMARY_MAX_LENGTH}："
                "它排在 instructions 最前面，过长会把「怎么调用」的规则挤出客户端可见窗口"
            )

    if not tasks:
        warnings.append("tasks/ 下没有任务模块（本项目可能只是壳，或任务放在别处）")
    for task in tasks:
        name = task["task"]
        if task["parse_error"]:
            warnings.append(f"{name}：文件无法解析，跳过（可能是 py2 风格）")
            continue
        if not task["docstring"]:
            todo.append(f"{name}：缺模块 docstring（AI 只能看到一个函数名）")
        else:
            first = task["docstring_first_paragraph"]
            if len(first) > FIRST_PARAGRAPH_MAX_LENGTH:
                warnings.append(
                    f"{name}：首段 {len(first)} 字符偏长（建议 ≤{FIRST_PARAGRAPH_MAX_LENGTH}）"
                    "——它会被塞进 instructions 的任务一览"
                )
            if not task["docstring_body"]:
                warnings.append(
                    f"{name}：docstring 只有首段，没有详细说明"
                    "（工具说明里就这一句，AI 无法判断边界行为）"
                )
        for item in task["missing_field_descriptions"]:
            todo.append(f"{name}：{item} 没有 Field(description=...)")

    return {"project": str(project_dir), "setting": setting, "tasks": tasks,
            "todo": todo, "warnings": warnings}


def render(report):
    lines = [f"项目：{report['project']}", ""]
    setting = report["setting"]
    lines.append("== setting.py（→ MCP instructions 的模块身份） ==")
    if not setting["exists"]:
        lines.append("  ✗ 文件不存在")
    else:
        for name in SETTING_FIELDS:
            value = (setting["values"].get(name) or "").strip()
            if not value:
                lines.append(f"  ✗ {name}: 缺失或为空")
                continue
            extra = f"（{len(value)} 字符）" if name == "project_summary" else ""
            shown = value.replace("\n", " ⏎ ")
            if len(shown) > 100:
                shown = shown[:100] + "…"
            lines.append(f"  ✓ {name}: {shown}{extra}")
    lines.append("")

    lines.append("== tasks/（→ MCP tools[].description） ==")
    if not report["tasks"]:
        lines.append("  （没有任务模块）")
    for task in report["tasks"]:
        if task["parse_error"]:
            lines.append(f"  ? {task['task']}: 无法解析")
            continue
        if task["docstring"]:
            first = task["docstring_first_paragraph"]
            flag = "✓"
            note = f"{len(task['docstring'])} 字符"
            lines.append(f"  {flag} {task['task']}: {first}  [{note}]")
        else:
            lines.append(f"  ✗ {task['task']}: 缺 docstring")
        missing = task["missing_field_descriptions"]
        total = sum(len(v) for v in task["models"].values())
        if total:
            state = "全部有描述" if not missing else "缺 " + ", ".join(missing)
            lines.append(f"      字段描述：{total} 个，{state}")
        else:
            lines.append("      字段描述：未在本文件找到 Params/Result（可能定义在别处）")
    lines.append("")

    if report["todo"]:
        lines.append(f"== 待办（{len(report['todo'])}） ==")
        lines += [f"  - {item}" for item in report["todo"]]
    else:
        lines.append("== 待办：无，元信息已齐全 ==")
    if report["warnings"]:
        lines.append("")
        lines.append(f"== 提醒（{len(report['warnings'])}） ==")
        lines += [f"  - {item}" for item in report["warnings"]]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="审计 FastTask 项目的 MCP 元信息（setting.py + 任务 docstring）"
    )
    parser.add_argument("project_dir", help="项目根目录（含 setting.py 与 tasks/）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"不是目录：{project_dir}", file=sys.stderr)
        return 2

    report = build_report(project_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 1 if report["todo"] else 0


if __name__ == "__main__":
    sys.exit(main())
