"""Persisted checkpoints and a bounded, observable workflow orchestrator."""
from __future__ import annotations

import copy
import difflib
import json
import re
import threading
import time
from pathlib import Path
from uuid import uuid4

from .contracts import ROLES, WorkflowError, draft_of, log, new_run, next_step, now, parse_input, parse_output, review_of
from .nodes import NodeRunner, static_checks


def sanitize_import(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkflowError("不是 CodeFlow 运行记录。")
    run = new_run(value.get("input"))
    records = value.get("records")
    if not isinstance(records, list) or len(records) > 5:
        raise WorkflowError("检查点步骤数量无效。")
    for record in records:
        if not isinstance(record, dict) or record.get("step") != next_step(run):
            raise WorkflowError("检查点顺序错误或包含重复节点。")
        stage = record["step"]
        output = parse_output(stage, record.get("output"), run["input"]["language"])
        metric = record.get("metrics", {})
        if not isinstance(metric, dict):
            raise WorkflowError("节点指标无效。")
        metrics = {}
        for k in ("calls", "tokens", "prompt_tokens", "completion_tokens", "duration_ms"):
            n = metric.get(k, 0)
            if type(n) is not int or not 0 <= n <= 100000000:
                raise WorkflowError("节点指标无效。")
            metrics[k] = n
        model = metric.get("model", "imported")
        if not isinstance(model, str) or len(model) > 100:
            raise WorkflowError("模型标识无效。")
        metrics["model"] = model
        run["records"].append({"step": stage, "role": "coder" if stage == "repair" else "reviewer" if stage == "recheck" else stage, "output": output, "metrics": metrics, "tool_events": []})
    run["status"] = "paused" if next_step(run) else "completed" if review_of(run)["approved"] else "needs_review"
    log(run, "已导入运行记录；不会自动调用模型。")
    return run


class RunStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.runs = {}
        for file in self.root.glob("*.json"):
            try:
                value = json.loads(file.read_text(encoding="utf-8"))
                if not re.fullmatch(r"[0-9a-f]{32}", value.get("id", "")) or file.stem != value["id"]:
                    continue
                checked = sanitize_import(value)
                # Keep owned records after validation; preserve logs and provenance.
                value["input"] = checked["input"]
                if len(value.get("logs", [])) > 200:
                    value["logs"] = value["logs"][-200:]
                value["active_step"] = None
                if value.get("status") in ("running", "queued"):
                    value["status"] = "paused"
                    log(value, "服务重启，已恢复成功节点，点击继续即可恢复。")
                self.runs[value["id"]] = value
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue

    def _write(self, run):
        target = self.root / (run["id"] + ".json")
        temporary = self.root / (run["id"] + ".tmp")
        temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows indexers/virus scanners can briefly hold the destination open.
        # Retry only the local atomic rename; never repeat a paid model request.
        for attempt in range(5):
            try:
                temporary.replace(target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * 2 ** attempt)

    def add(self, run):
        with self.lock:
            self._write(run)
            self.runs[run["id"]] = copy.deepcopy(run)
        return copy.deepcopy(run)

    def get(self, identifier):
        with self.lock:
            if identifier not in self.runs:
                raise KeyError("运行记录不存在。")
            return copy.deepcopy(self.runs[identifier])

    def update(self, identifier, mutate):
        with self.lock:
            run = copy.deepcopy(self.runs[identifier])
            mutate(run)
            run["updated_at"] = now()
            self._write(run)
            self.runs[identifier] = run
            return copy.deepcopy(run)

    def list(self):
        with self.lock:
            runs = sorted(self.runs.values(), key=lambda r: r["created_at"], reverse=True)[:50]
            return [{k: copy.deepcopy(r[k]) for k in ("id", "created_at", "updated_at", "status", "input")} | {"completed_steps": len(r["records"])} for r in runs]


class RunManager:
    def __init__(self, config, root, runner=None):
        self.config = config
        self.store = RunStore(root)
        self.runner = runner or NodeRunner(config)
        self.lock = threading.RLock()
        self.active = None
        self.stop_event = threading.Event()

    def create(self, value):
        with self.lock:
            if self.active:
                raise WorkflowError("已有任务正在执行，请完成或暂停后再新建。")
            run = new_run(value)
            if run["input"]["provider"] == "online" and not self.config.api_key:
                raise WorkflowError("请先配置 DeepSeek 密钥并重启，或载入离线演示。")
            self.store.add(run)
            return self.start(run["id"])

    def start(self, identifier):
        with self.lock:
            if self.active:
                raise WorkflowError("已有任务正在执行。")
            run = self.store.get(identifier)
            if next_step(run) is None:
                raise WorkflowError("当前任务已结束，请新建任务。")
            self.active = identifier
            self.stop_event.clear()
            def begin(r):
                r.update(status="running", error=None, active_step=None)
                log(r, "工作流开始；复用已完成的节点。")
            try:
                result = self.store.update(identifier, begin)
            except Exception:
                self.active = None
                raise
            threading.Thread(target=self._work, args=(identifier,), daemon=True).start()
            return result

    def pause(self, identifier):
        with self.lock:
            if self.active != identifier:
                raise WorkflowError("该任务未在运行。")
            self.stop_event.set()
            return self.store.update(identifier, lambda r: log(r, "已请求暂停：当前模型调用结束并保存结果后停止。"))

    def _work(self, identifier):
        try:
            while True:
                run = self.store.get(identifier)
                stage = next_step(run)
                if stage is None:
                    def finish(r):
                        passed = review_of(r)["approved"]
                        r.update(status="completed" if passed else "needs_review", active_step=None)
                        log(r, "工作流完成，静态审查通过。" if passed else "修复后仍有问题，已停止自动循环，需要人工复核。", "success" if passed else "warning")
                    self.store.update(identifier, finish)
                    return
                if self.stop_event.is_set():
                    def paused(r):
                        r.update(status="paused", active_step=None)
                        log(r, "检查点已保存，可继续未完成节点。")
                    self.store.update(identifier, paused)
                    return
                def entering(r):
                    r["active_step"] = stage
                    log(r, ROLES[stage] + "开始执行。")
                self.store.update(identifier, entering)
                def emit(event):
                    self.store.update(identifier, lambda r: log(r, ROLES[stage] + "：" + event["title"] + " · " + event["detail"]))
                record = self.runner.run(stage, self.store.get(identifier), emit)
                # Validate even injected/custom runners before persisting a checkpoint.
                record["output"] = parse_output(stage, record["output"], run["input"]["language"])
                def completed(r):
                    r["records"].append(record)
                    r["active_step"] = None
                    log(r, ROLES[stage] + "完成，结果已写入共享状态。", "success")
                    if stage == "reviewer" and not record["output"]["approved"]:
                        log(r, "审查未通过，转入一次修复与复审。", "warning")
                self.store.update(identifier, completed)
        except Exception as exc:
            message = self.runner.redact(str(exc)) if hasattr(self.runner, "redact") else str(exc)[:2000]
            def failed(r):
                r.update(status="error", active_step=None, error=message)
                log(r, message, "error")
            try:
                self.store.update(identifier, failed)
            except OSError:
                # Preserve completed nodes in memory if the disk becomes unwritable.
                with self.store.lock:
                    failed(self.store.runs[identifier])
        finally:
            with self.lock:
                self.active = None

    def snapshot(self, identifier):
        run = self.store.get(identifier)
        draft = draft_of(run)
        run["static_checks"] = static_checks(draft, run["input"]["language"])
        main = next((f for f in (draft or {}).get("files", []) if f["path"] != "README.md" and not f["path"].startswith("test_")), None)
        run["diff"] = "\n".join(difflib.unified_diff(run["input"]["code"].splitlines(), main["content"].splitlines() if main else [], fromfile="原始代码", tofile=main["path"] if main else "生成代码", lineterm=""))
        run["next_step"] = next_step(run)
        return run


def report_markdown(run):
    inp = run["input"]
    draft, review = draft_of(run), review_of(run)
    parts = ["# CodeFlow 交付报告", "", "## 任务", inp["task"], "", f"语言：{inp['language']} · 来源：{'离线固定演示' if inp['provider'] == 'demo' else 'DeepSeek'}", f"状态：{run['status']}", "", "## Agent 协作"]
    for r in run["records"]:
        parts.extend([f"- {ROLES[r['step']]}：{r['output']['summary']}"])
    if review:
        parts.extend(["", "## 审查结论", f"{'通过' if review['approved'] else '需要人工复核'} · {review['score']} 分", "模型审查与静态分析不等于测试通过；测试尚未在服务端执行。"])
        for f in review["findings"]:
            parts.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['message']} 建议：{f['suggestion']}")
    if draft:
        for f in draft["files"]:
            # Longer fence avoids code containing triple backticks breaking the report.
            parts.extend(["", "## " + f["path"], "``````" + (inp["language"] if f["path"] != "README.md" else "markdown"), f["content"], "``````"])
        parts.extend(["", "## 本地验证命令（未自动执行）", *["- `" + c + "`" for c in draft["commands"]]])
    return "\n".join(parts) + "\n"
