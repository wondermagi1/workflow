"""Workflow contracts, actual agent reuse, recovery and local HTTP boundaries."""
import io
import json
import threading
import time
import unittest
import zipfile
from http.client import HTTPConnection
from unittest.mock import patch

from agent.config import AgentConfig
from agent.llm import LLMError
from codeflow.contracts import DEMO_CODE, DEMO_TASK, WorkflowError, decode_output, draft_of, new_run, next_step, parse_input, parse_output
from codeflow.nodes import JsonClient, NodeRunner, demo_output
from codeflow.runtime import RunManager, RunStore, sanitize_import
from codeflow.server import make_server
from tests import make_workspace, remove_workspace


def demo_input():
    return {"task": DEMO_TASK, "code": DEMO_CODE, "mode": "repair", "language": "python", "provider": "demo"}


def settle(manager):
    end = time.monotonic() + 5
    while manager.active and time.monotonic() < end:
        time.sleep(0.005)
    if manager.active:
        raise AssertionError("Worker did not settle")


class WorkflowContractsTests(unittest.TestCase):
    def test_input_rejects_empty_and_unsupported_custom_demo(self):
        for inp in ({}, {**demo_input(), "task": "任意生成需求"}, {**demo_input(), "language": "ruby"}, {**demo_input(), "code": ""}):
            with self.subTest(inp=inp), self.assertRaises(WorkflowError):
                parse_input(inp)

    def test_source_whitespace_is_preserved_for_code_and_diff(self):
        source = "\n\ndef example():\n    return 1\n"
        self.assertEqual(parse_input({**demo_input(), "provider": "online", "code": source})["code"], source)

    def test_output_rejects_unsafe_paths_and_missing_test_file(self):
        for paths in (("../escape.py", "test_solution.py"), ("solution.py", "solution.py"), ("solution.py", ".env")):
            output = demo_output("coder")
            for file, name in zip(output["files"], paths):
                file["path"] = name
            with self.subTest(paths=paths), self.assertRaises(WorkflowError):
                parse_output("coder", output, "python")

    def test_review_cannot_approve_with_high_issue_or_failed_acceptance(self):
        for field in ("findings", "checklist", "score"):
            output = demo_output("recheck")
            output[field] = demo_output("reviewer")[field]
            with self.subTest(field=field), self.assertRaises(WorkflowError):
                parse_output("recheck", output, "python")

    def test_malformed_output_fails_instead_of_becoming_success(self):
        for value in ("not json", "[]", "{}", '{"summary": "x"}'):
            with self.subTest(value=value), self.assertRaises(WorkflowError):
                decode_output("planner", value, "python")

    def test_bounded_repair_loop_terminates_even_after_second_rejection(self):
        run = new_run(demo_input())
        expected = ["planner", "coder", "reviewer", "repair", "recheck"]
        for stage in expected:
            self.assertEqual(next_step(run), stage)
            run["records"].append({"step": stage, "output": demo_output("reviewer" if stage == "recheck" else stage)})
        self.assertIsNone(next_step(run))
        self.assertEqual(draft_of(run), demo_output("repair"))


class WorkflowRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = make_workspace("workflow-")
        self.config = AgentConfig(workspace=self.root, api_key="unit-test-secret", base_url="https://api.deepseek.com", model="deepseek-flash")
    def tearDown(self):
        remove_workspace(self.root)

    def test_atomic_save_retries_transient_file_lock_but_propagates_persistent_failure(self):
        store = RunStore(self.root / "runs")
        run = new_run(demo_input())
        original_replace = type(self.root).replace
        calls = []
        def transient_lock(source, target):
            calls.append(source)
            if len(calls) == 1:
                raise PermissionError("temporary file lock")
            return original_replace(source, target)
        with patch("codeflow.runtime.Path.replace", transient_lock), patch("codeflow.runtime.time.sleep") as sleep:
            store.add(run)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(0.02)
        self.assertEqual(RunStore(self.root / "runs").get(run["id"])["input"], run["input"])
        other = new_run(demo_input())
        with patch("codeflow.runtime.Path.replace", side_effect=PermissionError("persistent lock")) as replace, patch("codeflow.runtime.time.sleep"):
            with self.assertRaises(PermissionError):
                store.add(other)
        self.assertEqual(replace.call_count, 5)
        self.assertNotIn(other["id"], store.runs)

    def test_demo_uses_original_codeagent_without_paid_http_and_persists_all_nodes(self):
        manager = RunManager(self.config, self.root / "runs")
        with patch("urllib.request.urlopen", side_effect=AssertionError("Offline must not call HTTP")):
            run = manager.create(demo_input())
            settle(manager)
        run = manager.snapshot(run["id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual([r["step"] for r in run["records"]], ["planner", "coder", "reviewer", "repair", "recheck"])
        self.assertTrue(all(r["metrics"]["tokens"] == 0 for r in run["records"]))
        self.assertIn("return result", draft_of(run)["files"][0]["content"])
        self.assertIn("+", run["diff"])
        self.assertNotIn('return list(set(items))+', run["diff"])
        self.assertTrue(all(not c["analysis"]["parse_error"] for c in run["static_checks"]))
        self.assertEqual(len(RunStore(self.root / "runs").list()), 1)

    def test_online_nodes_independently_receive_required_shared_context(self):
        run = new_run({**demo_input(), "provider": "online"})
        seen = []
        stage_iter = iter(("planner", "coder", "recheck"))
        def respond(client, payload):
            seen.append(payload)
            content = json.dumps(demo_output(next(stage_iter)), ensure_ascii=False)
            return {"choices": [{"finish_reason": "stop", "message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        with patch.object(JsonClient, "_post_with_retry", respond):
            runner = NodeRunner(self.config)
            for stage in ("planner", "coder", "reviewer"):
                run["records"].append(runner.run(stage, run))
        self.assertIsNone(next_step(run))
        self.assertEqual(len(seen), 3)
        self.assertNotIn("需求规划师", seen[1]["messages"][0]["content"])
        self.assertIn('"plan"', seen[1]["messages"][1]["content"])
        self.assertIn('"draft"', seen[2]["messages"][1]["content"])
        self.assertIn('"static_checks"', seen[2]["messages"][1]["content"])
        self.assertEqual(run["records"][-1]["metrics"]["tokens"], 30)
        self.assertTrue(all(p["thinking"]["type"] == "disabled" for p in seen))
        self.assertTrue(all("unit-test-secret" not in json.dumps(p) for p in seen))

    def test_pause_saves_current_result_and_resume_never_repeats_successful_stage(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class GatedRunner(NodeRunner):
            def run(self, stage, run, emit):
                calls.append(stage)
                if stage == "planner":
                    entered.set()
                    release.wait(3)
                return super().run(stage, run, emit)
        manager = RunManager(self.config, self.root / "runs", GatedRunner(self.config))
        run = manager.create(demo_input())
        self.assertTrue(entered.wait(2))
        with self.assertRaises(WorkflowError):
            manager.create(demo_input())
        manager.pause(run["id"])
        release.set()
        settle(manager)
        self.assertEqual(manager.store.get(run["id"])["status"], "paused")
        self.assertEqual(calls, ["planner"])
        manager.start(run["id"])
        settle(manager)
        self.assertEqual(calls.count("planner"), 1)
        self.assertEqual(manager.store.get(run["id"])["status"], "completed")

    def test_failure_can_retry_missing_node_and_redacts_runtime_secret(self):
        attempts = []
        class FailingRunner(NodeRunner):
            def run(self, stage, run, emit):
                attempts.append(stage)
                if stage == "coder" and attempts.count("coder") == 1:
                    raise WorkflowError("network error unit-test-secret")
                return super().run(stage, run, emit)
        manager = RunManager(self.config, self.root / "runs", FailingRunner(self.config))
        run = manager.create(demo_input()); settle(manager)
        saved = manager.store.get(run["id"])
        self.assertEqual(saved["status"], "error")
        self.assertEqual(len(saved["records"]), 1)
        self.assertNotIn("unit-test-secret", json.dumps(saved))
        manager.start(run["id"]); settle(manager)
        self.assertEqual(attempts.count("planner"), 1)
        final = manager.store.get(run["id"])
        self.assertEqual(final["status"], "completed", final.get("error"))

    def test_restart_and_import_preserve_valid_checkpoints_but_require_explicit_resume(self):
        run = new_run(demo_input())
        run["records"] = [NodeRunner(self.config).run("planner", run)]
        run["status"] = "running"
        store = RunStore(self.root / "runs"); store.add(run)
        restored = RunStore(self.root / "runs").get(run["id"])
        self.assertEqual(restored["status"], "paused")
        imported = sanitize_import(run)
        self.assertEqual(imported["status"], "paused")
        self.assertNotEqual(imported["id"], run["id"])
        self.assertEqual(next_step(imported), "coder")
        damaged = {**run, "records": run["records"] * 2}
        with self.assertRaises(WorkflowError):
            sanitize_import(damaged)

    def test_truncated_response_is_not_accepted(self):
        client = JsonClient(self.config)
        with patch.object(client, "_post_with_retry", return_value={"choices": [{"finish_reason": "length"}]}), self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "JSON"}])


class WorkflowHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make_workspace("workflow-http-")
        config = AgentConfig(workspace=cls.root, api_key="http-test-secret", model="deepseek-flash")
        cls.server = make_server(config, port=0, root=cls.root / "runs")
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); remove_workspace(cls.root)
    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=None if body is None else json.dumps(body), headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse(); raw = response.read(); status = response.status; connection.close()
        return status, raw
    def test_status_is_public_config_without_any_key_representation(self):
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn(b"http-test-secret", body)
        self.assertNotIn(b"api_key", body)
        self.assertEqual(json.loads(body)["application"], "CodeFlow")
    def test_static_assets_and_path_boundaries(self):
        for path in ("/", "/static/app.js", "/static/style.css"):
            self.assertEqual(self.request("GET", path)[0], 200)
        self.assertEqual(self.request("GET", "/.env")[0], 404)
        self.assertEqual(self.request("GET", "/static/../contracts.py")[0], 404)
    def test_cross_origin_and_untrusted_host_rejected(self):
        self.assertEqual(self.request("POST", "/api/runs", demo_input(), {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/status", headers={"Host": "evil.example"})[0], 403)
    def test_end_to_end_http_exports_valid_zip_json_and_report(self):
        status, body = self.request("POST", "/api/runs", demo_input())
        self.assertEqual(status, 201)
        identifier = json.loads(body)["id"]; settle(self.server.manager)
        for name in ("export.json", "report.md", "bundle.zip"):
            status, data = self.request("GET", f"/api/runs/{identifier}/{name}")
            self.assertEqual(status, 200)
            self.assertNotIn(b"http-test-secret", data)
            if name == "bundle.zip":
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertEqual(set(archive.namelist()), {"solution.py", "test_solution.py", "CODEFLOW_REPORT.md"})
            elif name == "export.json":
                self.assertEqual(json.loads(data)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
