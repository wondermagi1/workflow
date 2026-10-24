"""Browser/server contracts, dependency order and bounded model output."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

LANGUAGES = {"python": ("solution.py", "test_solution.py"), "javascript": ("solution.js", "test_solution.js"), "typescript": ("solution.ts", "test_solution.ts")}
ROLES = {"planner": "需求规划师", "coder": "编码工程师", "reviewer": "代码审查师", "repair": "编码工程师 · 修复", "recheck": "代码审查师 · 复审"}
DEMO_TASK = "修复列表去重函数，要求保留原始顺序，支持空列表，不修改输入，并补充边界测试。"
DEMO_CODE = "def unique_items(items):\n    return list(set(items))\n"


class WorkflowError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(value, label, maximum=2000, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise WorkflowError(f"{label}需要 {minimum}–{maximum} 字符。")
    return value.strip()


def texts(value, label, maximum=12, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        count = str(len(value)) if isinstance(value, list) else "非数组"
        raise WorkflowError(f"{label}需要 {minimum}–{maximum} 个数组条目，实际为 {count}。")
    return [text(item, label, 1500) for item in value]


def parse_input(value):
    if not isinstance(value, dict):
        raise WorkflowError("任务需要是一个对象。")
    language = value.get("language", "python")
    provider = value.get("provider", "online")
    mode = value.get("mode", "generate")
    if language not in LANGUAGES or provider not in ("online", "demo") or mode not in ("generate", "repair"):
        raise WorkflowError("语言、任务类型或模型来源无效。")
    task = text(value.get("task"), "任务描述", 3000)
    code = value.get("code", "")
    if not isinstance(code, str) or len(code) > 20000:
        raise WorkflowError("已有代码需要是最多 20,000 字符的文本。")
    if mode == "repair" and not code.strip():
        raise WorkflowError("修复任务需要提供已有代码。")
    if provider == "demo" and (task != DEMO_TASK or language != "python" or code.strip() != DEMO_CODE.strip() or mode != "repair"):
        raise WorkflowError("离线模式仅演示内置的列表去重修复案例，请点击“载入离线演示”；自定义任务请选择 DeepSeek。")
    return {"task": task, "code": code, "language": language, "provider": provider, "mode": mode}


def parse_output(stage, value, language):
    if not isinstance(value, dict):
        raise WorkflowError("Agent 返回的 JSON 需要是对象。")
    if stage == "planner":
        return {"summary": text(value.get("summary"), "规划摘要"), "acceptance": texts(value.get("acceptance"), "验收标准", minimum=1), "approach": texts(value.get("approach"), "实现步骤", minimum=1), "risks": texts(value.get("risks", []), "风险")}
    if stage in ("coder", "repair"):
        files = value.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 4:
            raise WorkflowError("编码结果需要包含 1–4 个文件。")
        allowed = {*LANGUAGES[language], "README.md"}
        clean = []
        for item in files:
            if not isinstance(item, dict) or item.get("path") not in allowed:
                raise WorkflowError("产物文件名无效，不能写入其他路径。")
            content = text(item.get("content"), "文件内容", 30000)
            clean.append({"path": item["path"], "content": content + "\n"})
        names = [f["path"] for f in clean]
        if len(names) != len(set(names)) or not set(LANGUAGES[language]).issubset(names):
            raise WorkflowError("编码结果需要包含实现文件和测试文件，文件名不能重复。")
        return {"summary": text(value.get("summary"), "实现摘要"), "files": clean, "notes": texts(value.get("notes", []), "实现说明"), "commands": texts(value.get("commands", []), "运行命令", maximum=4)}
    findings = value.get("findings", [])
    if not isinstance(findings, list) or len(findings) > 12:
        raise WorkflowError("审查问题数量无效。")
    clean = []
    for f in findings:
        if not isinstance(f, dict) or f.get("severity") not in ("high", "medium", "low") or f.get("file") not in {*LANGUAGES[language], "README.md"} or type(f.get("line")) is not int or not 0 <= f["line"] <= 100000:
            raise WorkflowError("审查问题位置或等级无效。")
        clean.append({"severity": f["severity"], "file": f["file"], "line": f["line"], "message": text(f.get("message"), "问题说明"), "suggestion": text(f.get("suggestion"), "修复建议")})
    score, approved = value.get("score"), value.get("approved")
    if type(score) is not int or not 0 <= score <= 100 or type(approved) is not bool:
        raise WorkflowError("审查需要布尔结论和 0–100 整数分数。")
    if approved and (score < 80 or any(f["severity"] == "high" for f in clean)):
        raise WorkflowError("存在高等级问题或评分低于 80 时不能判定通过。")
    checks = value.get("checklist", [])
    if not isinstance(checks, list) or not 1 <= len(checks) <= 12:
        raise WorkflowError("审查需要包含验收清单。")
    checklist = []
    for c in checks:
        if not isinstance(c, dict) or c.get("status") not in ("pass", "fail", "unknown"):
            raise WorkflowError("验收项状态无效。")
        checklist.append({"item": text(c.get("item"), "验收项", 300), "status": c["status"], "detail": text(c.get("detail"), "验收说明")})
    if approved and any(c["status"] == "fail" for c in checklist):
        raise WorkflowError("存在未通过的验收项，不能判定审查通过。")
    return {"approved": approved, "score": score, "summary": text(value.get("summary"), "审查摘要"), "findings": clean, "checklist": checklist}


def decode_output(stage, content, language):
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        return parse_output(stage, json.loads(content), language)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise WorkflowError("Agent 未返回符合合同的 JSON，请重试当前节点。") from exc


def outputs(run):
    return {r["step"]: r["output"] for r in run["records"]}


def next_step(run):
    done = outputs(run)
    for stage in ("planner", "coder", "reviewer"):
        if stage not in done:
            return stage
    if done["reviewer"]["approved"]:
        return None
    for stage in ("repair", "recheck"):
        if stage not in done:
            return stage
    return None


def draft_of(run):
    done = outputs(run)
    return done.get("repair") or done.get("coder")


def review_of(run):
    done = outputs(run)
    return done.get("recheck") or done.get("reviewer")


def new_run(value):
    return {"schema_version": 1, "id": uuid4().hex, "created_at": now(), "updated_at": now(), "status": "queued", "input": parse_input(value), "records": [], "logs": [], "active_step": None, "error": None}


def log(run, message, kind="info"):
    run["logs"] = (run["logs"] + [{"time": now(), "message": message, "kind": kind}])[-200:]
    run["updated_at"] = now()
