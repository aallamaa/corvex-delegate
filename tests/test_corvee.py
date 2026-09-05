#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import sys
import shutil
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from corvee_config import write_configuration
import corvee
import corvee_config
import install as skill_install
import configure_corvee
import io
from contextlib import redirect_stderr


SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "corvee.py"
CONFIGURE = SKILL_ROOT / "scripts" / "configure_corvee.py"
INSTALL = SKILL_ROOT / "scripts" / "install.py"
CLI = SKILL_ROOT / "scripts" / "corvee"


class MockHandler(BaseHTTPRequestHandler):
    calls = 0
    tools_seen: list[str] = []

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        # Corvex's catalog is public even when the supplied key is invalid.
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = {"data": [{"id": "delegate-b"}, {"id": "delegate-a"}]}
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self) -> None:
        if self.headers.get("Authorization") not in {
            "Bearer test-secret",
            "Bearer file-secret",
        }:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
            return
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        if "tools" not in payload:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode())
            return
        if self.path.endswith("/responses"):
            function_call = {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "call-1",
                "name": "compatibility_probe",
                "arguments": '{"value":"ok"}',
                "status": "completed",
            }
            events = [
                {"type": "response.created", "response": {"id": "resp-1", "output": []}},
                {"type": "response.output_item.done", "item": function_call},
                {
                    "type": "response.completed",
                    "response": {"id": "resp-1", "status": "completed", "output": [function_call]},
                },
            ]
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body.encode())
            return
        MockHandler.tools_seen = [tool["function"]["name"] for tool in payload["tools"]]
        MockHandler.calls += 1
        if MockHandler.calls == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "sample.txt"}),
                        },
                    }
                ],
            }
        else:
            tool_result = json.loads(payload["messages"][-1]["content"])
            message = {
                "role": "assistant",
                "content": f"Evidence report: {tool_result['result']}",
            }
        response = {"choices": [{"message": message}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


class CorveeTest(unittest.TestCase):
    def setUp(self) -> None:
        MockHandler.calls = 0
        MockHandler.tools_seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.environment = os.environ | {
            "CORVEX_API_KEY": "test-secret",
            "CORVEX_API_URL": self.base_url,
            "CORVEX_MODEL": "delegate-a",
        }

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_lists_models(self) -> None:
        result = subprocess.run(
            ["python3", str(SCRIPT), "--no-config", "--models"],
            env=self.environment,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout, "delegate-a\ndelegate-b\n")
        self.assertNotIn("test-secret", result.stdout + result.stderr)

    def test_public_cli_and_project_model_preserve_credential_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "settings" / "config.toml"
            project = root / "project"
            project.mkdir()
            mission = project / "mission.md"
            mission.write_text("Report only.\n")
            model_config = project / "DELEGATE.json"
            model_config.write_text('{"model":"delegate-b"}')
            write_configuration(config, base_url=self.base_url, api_key="file-secret", model="delegate-a")
            env = {k:v for k,v in self.environment.items() if k not in {"CORVEX_API_KEY", "CORVEX_API_URL", "CORVEX_MODEL"}}
            listed = subprocess.run([sys.executable, str(CLI), "models", "--config", str(config)], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(listed.stdout, "delegate-a\ndelegate-b\n")
            result = subprocess.run([sys.executable, str(CLI), "run", "--config", str(config), "--model-config", str(model_config), "--mission", str(mission), "--cwd", str(project), "--dry-run"], env=env, capture_output=True, text=True, check=True)
            self.assertIn('"model": "delegate-b"', result.stderr)
            self.assertIn(str(config), result.stderr)
            self.assertNotIn("file-secret", result.stdout + result.stderr)

    def test_loads_generic_names_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                f"API_URL={self.base_url}\nAPI_KEY=file-secret\n", encoding="utf-8"
            )
            clean_environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"CORVEX_API_KEY", "CORVEX_API_URL", "CORVEX_MODEL"}
            }
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--no-config",
                    "--env-file",
                    str(env_file),
                    "--models",
                ],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout, "delegate-a\ndelegate-b\n")
            self.assertNotIn("file-secret", result.stdout + result.stderr)

    def test_model_config_selects_model_without_storing_a_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mission = root / "mission.md"
            mission.write_text("No-op.\n", encoding="utf-8")
            model_config = root / "DELEGATE.json"
            model_config.write_text('{"model":"selected-delegate"}\n', encoding="utf-8")
            environment = self.environment | {"CORVEX_MODEL": "environment-delegate"}
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--no-config",
                    "--mission",
                    str(mission),
                    "--cwd",
                    str(root),
                    "--model-config",
                    str(model_config),
                    "--dry-run",
                ],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn('"model": "selected-delegate"', result.stderr)
            self.assertNotIn("test-secret", result.stdout + result.stderr)

    def test_read_only_agent_uses_only_read_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("hello\n", encoding="utf-8")
            mission = root / "mission.md"
            mission.write_text("Read sample.txt and report it.\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--no-config",
                    "--mission",
                    str(mission),
                    "--cwd",
                    str(root),
                    "--max-steps",
                    "3",
                ],
                env=self.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("1: hello", result.stdout)
            self.assertNotIn("write_file", MockHandler.tools_seen)
            self.assertNotIn("replace_text", MockHandler.tools_seen)
            self.assertNotIn("run_command", MockHandler.tools_seen)
            self.assertNotIn("test-secret", result.stdout + result.stderr)

    def test_write_dry_run_does_not_expose_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mission = root / "mission.md"
            mission.write_text("No-op.\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--no-config",
                    "--mission",
                    str(mission),
                    "--cwd",
                    str(root),
                    "--write",
                    "--allow-command",
                    "cargo",
                    "--dry-run",
                ],
                env=self.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn('"mode": "write"', result.stderr)
            self.assertIn('"cargo"', result.stderr)
            self.assertNotIn("test-secret", result.stdout + result.stderr)

    def test_configure_then_use_saved_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "corvee" / "config.toml"
            configured = subprocess.run(
                [
                    "python3",
                    str(CONFIGURE),
                    "--config",
                    str(config),
                    "configure",
                    "--url",
                    self.base_url,
                    "--model",
                    "delegate-a",
                    "--api-key-env",
                    "CORVEX_API_KEY",
                    "--non-interactive",
                ],
                env=self.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertNotIn("test-secret", configured.stdout + configured.stderr)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            credentials = config.parent / "credentials.toml"
            self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)

            clean_environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"CORVEX_API_KEY", "CORVEX_API_URL", "CORVEX_MODEL"}
            }
            listed = subprocess.run(
                ["python3", str(SCRIPT), "--config", str(config), "--models"],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(listed.stdout, "delegate-a\ndelegate-b\n")
            self.assertNotIn("test-secret", listed.stdout + listed.stderr)

            reconfigured = subprocess.run(
                [
                    "python3",
                    str(CONFIGURE),
                    "--config",
                    str(config),
                    "configure",
                    "--url",
                    self.base_url,
                    "--non-interactive",
                ],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Default model: delegate-a", reconfigured.stdout)
            self.assertNotIn("test-secret", reconfigured.stdout + reconfigured.stderr)

            subprocess.run(
                [
                    "python3",
                    str(CONFIGURE),
                    "--config",
                    str(config),
                    "select",
                    "delegate-b",
                ],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            shown = subprocess.run(
                ["python3", str(CONFIGURE), "--config", str(config), "show"],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Model: delegate-b", shown.stdout)

            subprocess.run(
                [
                    "python3",
                    str(CONFIGURE),
                    "--config",
                    str(config),
                    "select",
                    "auto",
                ],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            shown_auto = subprocess.run(
                ["python3", str(CONFIGURE), "--config", str(config), "show"],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Model: not selected", shown_auto.stdout)

    def test_installer_validates_before_finalizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex"
            installed = subprocess.run(
                [
                    "python3",
                    str(INSTALL),
                    "--codex-home",
                    str(codex_home),
                    "--url",
                    self.base_url,
                    "--model",
                    "delegate-a",
                    "--api-key-env",
                    "CORVEX_API_KEY",
                    "--non-interactive",
                    "--source",
                    str(SKILL_ROOT),
                ],
                env=self.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            target = codex_home / "skills" / "corvee"
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertFalse((target / ".env").exists())
            self.assertTrue((codex_home / "corvee" / "config.toml").is_file())
            self.assertNotIn("test-secret", installed.stdout + installed.stderr)

            bad_home = Path(directory) / "bad-codex"
            bad_environment = self.environment | {"CORVEX_API_KEY": "wrong-secret"}
            rejected = subprocess.run(
                [
                    "python3",
                    str(INSTALL),
                    "--codex-home",
                    str(bad_home),
                    "--url",
                    self.base_url,
                    "--api-key-env",
                    "CORVEX_API_KEY",
                    "--non-interactive",
                    "--source",
                    str(SKILL_ROOT),
                ],
                env=bad_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse((bad_home / "skills" / "corvee").exists())
            self.assertFalse((bad_home / "corvee" / "config.toml").exists())
            self.assertNotIn("wrong-secret", rejected.stdout + rejected.stderr)

    def test_installs_native_provider_and_custom_agent_after_responses_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "corvee" / "config.toml"
            codex_config = root / "config.toml"
            agent_file = root / "agents" / "corvee.toml"
            subprocess.run(
                [
                    "python3",
                    str(CONFIGURE),
                    "--config",
                    str(config),
                    "configure",
                    "--url",
                    self.base_url,
                    "--model",
                    "delegate-a",
                    "--api-key-env",
                    "CORVEX_API_KEY",
                    "--non-interactive",
                ],
                env=self.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            installed = subprocess.run(
                [
                    "python3",
                    str(CONFIGURE),
                    "--config",
                    str(config),
                    "--codex-config",
                    str(codex_config),
                    "--agent-file",
                    str(agent_file),
                    "install-agent",
                ],
                env=self.environment,
                text=True,
                capture_output=True,
                check=True,
            )
            provider_text = codex_config.read_text(encoding="utf-8")
            agent_text = agent_file.read_text(encoding="utf-8")
            self.assertIn("[model_providers.corvex]", provider_text)
            self.assertIn('wire_api = "responses"', provider_text)
            self.assertIn('name = "corvee"', agent_text)
            self.assertIn('model = "delegate-a"', agent_text)
            self.assertNotIn("test-secret", provider_text + agent_text + installed.stdout)
            self.assertEqual(agent_file.stat().st_mode & 0o777, 0o600)


class FinalAuditFixTest(unittest.TestCase):
    def test_select_auto_is_offline_and_needs_no_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('model = "old"\n')
            with patch.object(sys, "argv", ["configure", "--config", str(config), "select", "auto"]), patch.object(configure_corvee, "checked_settings", side_effect=AssertionError("must not access credentials/network")):
                self.assertEqual(configure_corvee.main(), 0)
            self.assertEqual(corvee_config.load_config(config)["model"], "")

    def test_reconfigure_preserves_custom_credential_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            corvee_config.write_configuration(config, base_url=corvee_config.DEFAULT_BASE_URL,
                api_key="old", model="mock", api_key_env="CUSTOM_KEY",
                credentials_file="private/key.toml", default_complexity="low")
            args = configure_corvee.parser().parse_args(["--config", str(config), "configure", "--non-interactive"])
            with patch.dict(os.environ, {"CUSTOM_KEY": "new"}), patch.object(configure_corvee, "fetch_models", return_value=["mock"]), patch.object(configure_corvee, "verify_credential"):
                configure_corvee.configure(args)
            settings = corvee_config.load_config(config)
            self.assertEqual(settings["credentials_file"], "private/key.toml")
            self.assertEqual(settings["api_key_env"], "CUSTOM_KEY")
            self.assertEqual(settings["default_complexity"], "low")
            self.assertFalse((config.parent / "credentials.toml").exists())
            self.assertIn('"new"', (config.parent / "private/key.toml").read_text())

    def test_native_probe_uses_requested_effort(self):
        from urllib.error import HTTPError
        captured = []
        def reject(req, timeout):
            captured.append(json.loads(req.data))
            raise HTTPError(req.full_url, 400, "unsupported effort", None, io.BytesIO(b"private error"))
        with patch.object(corvee_config, "open_request", side_effect=reject), self.assertRaises(corvee_config.ConfigError):
            corvee_config.probe_responses_api("https://example.com", "fake", "mock", reasoning_effort="xhigh")
        self.assertEqual(captured[0]["reasoning"]["effort"], "xhigh")

    def test_custom_credential_environment_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            args = configure_corvee.parser().parse_args([
                "--config", str(config), "configure", "--non-interactive",
                "--api-key-env", "CUSTOM_KEY", "--model", "mock"])
            with patch.dict(os.environ, {"CUSTOM_KEY": "correct", "CORVEX_API_KEY": "wrong"}), patch.object(configure_corvee, "fetch_models", return_value=["mock"]), patch.object(configure_corvee, "verify_credential"):
                configure_corvee.configure(args)
                settings = corvee_config.load_config(config)
                self.assertEqual(settings["api_key_env"], "CUSTOM_KEY")
                self.assertEqual(corvee_config.resolve_api_key(settings, config), "correct")

    def test_config_and_credential_paths_cannot_collide(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(corvee_config.ConfigError):
            corvee_config.write_configuration(Path(directory) / "credentials.toml", base_url=corvee_config.DEFAULT_BASE_URL, api_key="fake", model=None)

    def test_install_cleanup_failure_is_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(sys, "argv", ["install", "--codex-home", str(root), "--non-interactive"]), patch.object(skill_install.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), patch.object(skill_install, "remove_backup", side_effect=OSError("cleanup failed")), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(skill_install.main(), 0)
            self.assertTrue((root / "skills/corvee/SKILL.md").is_file())
            self.assertIn("Installed successfully", stderr.getvalue())

    def test_missing_env_file_is_clean_error(self):
        with patch.object(sys, "argv", ["install", "--from-env-file", "/nonexistent-corvee-audit-input"]), redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(skill_install.main(), 2)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_provider_deadline_interrupts_streaming_reads(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            with corvee_config.request_deadline(0.05):
                time.sleep(2)
        self.assertLess(time.monotonic() - started, 1)

    def test_dotenv_inline_comments_and_quoted_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.env"
            path.write_text('API_KEY=sample # comment\nVALUE="quoted # still" # comment\nHASH=abc#123\n')
            self.assertEqual(corvee_config.load_env_file(path), {"API_KEY": "sample", "VALUE": "quoted # still", "HASH": "abc#123"})

    def test_file_tool_runtime_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "file.txt"
            path.write_text("x")
            tools = corvee.RepositoryTools(root, True, set())
            with self.assertRaises(ValueError):
                tools.tool_read_file("file.txt", line_count=2000)
            with self.assertRaises(ValueError):
                tools.tool_replace_text("file.txt", "", "y", 2)
            with self.assertRaises(ValueError):
                tools.tool_write_file("file.txt", "x" * (corvee.MAX_FILE_BYTES + 1))
            self.assertEqual(path.read_text(), "x")
            path.write_text("x" * (corvee.MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError):
                tools.tool_replace_text("file.txt", "x", "y")


class RunnerRecoveryTest(unittest.TestCase):
    def response(self, content=None, calls=None):
        return {"choices": [{"message": {"content": content, "tool_calls": calls or []}}]}

    def tool_call(self):
        return {"id": "call-1", "function": {"name": "list_files", "arguments": "{}"}}

    def test_default_request_timeout_is_600(self):
        self.assertEqual(corvee.build_parser().parse_args([]).http_timeout, 600)
        self.assertEqual(corvee.ApiClient("https://example.com", "fake").timeout, 600)

    def test_final_step_disables_tools_and_marks_report_incomplete(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", return_value=self.response("partial findings")) as call:
            result = corvee.run_steps(client, tools, [], "mock", None, 1, 100)
        self.assertEqual(result, 3)
        self.assertEqual(call.call_args.args[2]["tool_choice"], "none")

    def test_disabled_tools_are_not_executed_during_wrapup(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", return_value=self.response(calls=[self.tool_call()])), patch.object(tools, "execute") as execute:
            self.assertEqual(corvee.run_steps(client, tools, [], "mock", None, 1, 100), 3)
        execute.assert_not_called()

    def test_transient_retry_does_not_replay_completed_tool(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", side_effect=[self.response(calls=[self.tool_call()]), corvee.ProviderFailure("request_timeout", True), self.response("done")]), patch.object(tools, "execute", return_value='{"ok":true,"result":"file"}') as execute, patch.object(corvee.time, "sleep"):
            self.assertEqual(corvee.run_steps(client, tools, [], "mock", None, 5, 100), 0)
        self.assertEqual(execute.call_count, 1)

    def test_retry_limit_and_permanent_failure(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        for retryable, expected_calls, code in [(True, 2, 75), (False, 1, 1)]:
            client = corvee.ApiClient("https://example.com", "fake")
            with patch.object(client, "call", side_effect=corvee.ProviderFailure("failure", retryable)) as call, patch.object(corvee.time, "sleep"), self.assertRaises(SystemExit) as error:
                corvee.run_steps(client, tools, [], "mock", None, 5, 100)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(call.call_count, expected_calls)

    def test_socket_timeout_is_classified_without_traceback(self):
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(corvee, "open_request", side_effect=TimeoutError("sensitive error")), self.assertRaises(corvee.ProviderFailure) as error:
            client.call("POST", "/chat/completions", {})
        self.assertTrue(error.exception.retryable)
        self.assertEqual(str(error.exception), "request_timeout")

    def test_repeated_results_trigger_wrapup(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", side_effect=[self.response(calls=[self.tool_call()])] * 3 + [self.response("partial")]) as call, patch.object(tools, "execute", return_value='{"ok":true,"result":"same"}') as execute:
            result = corvee.run_steps(client, tools, [], "mock", None, 20, 100)
        self.assertEqual(result, 3)
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(call.call_args.args[2]["tool_choice"], "none")

    def test_private_checkpoint_preserves_partial_results_and_redacts_key(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = corvee.RunJournal(Path(directory) / "run", "fake-secret")
            journal.checkpoint([{"role": "tool", "content": "partial fake-secret"}], "tool_pending")
            journal.finish("interrupted", 130)
            checkpoint = json.loads((journal.directory / "checkpoint.json").read_text())
            self.assertIn("partial [REDACTED]", checkpoint["messages"][0]["content"])
            self.assertFalse(checkpoint["automatic_replay_safe"])
            self.assertEqual(checkpoint["phase"], "tool_pending")
            for file in journal.directory.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("fake-secret", file.read_text())
            self.assertEqual(journal.directory.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                corvee.RunJournal(journal.directory, "fake")

    def test_failed_run_keeps_completed_tools_and_incomplete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mission = root / "mission.md"
            mission.write_text("Read-only audit")
            artifacts = root / "artifacts"
            argv = ["corvee", "--no-config", "--config", str(root / "config.toml"),
                    "--mission", str(mission), "--cwd", str(root), "--model", "mock",
                    "--run-dir", str(artifacts), "--request-retries", "0"]
            with patch.object(sys, "argv", argv), patch.dict(os.environ, {"CORVEX_API_KEY": "fake-secret"}), patch.object(corvee.ApiClient, "call", side_effect=[self.response(calls=[self.tool_call()]), corvee.ProviderFailure("request_timeout", True)]), patch.object(corvee.RepositoryTools, "execute", return_value='{"ok":true,"result":"saved evidence"}'), self.assertRaises(SystemExit) as error:
                corvee.main()
            self.assertEqual(error.exception.code, 75)
            self.assertEqual(json.loads((artifacts / "status.json").read_text())["exit_code"], 75)
            checkpoint = json.loads((artifacts / "checkpoint.json").read_text())
            self.assertTrue(any("saved evidence" in str(message) for message in checkpoint["messages"]))
            self.assertIn("Incomplete run", (artifacts / "report.md").read_text())
            events = [json.loads(line) for line in (artifacts / "events.jsonl").read_text().splitlines()]
            self.assertTrue(any(event["event"] == "tool_end" for event in events))
            self.assertNotIn("saved evidence", (artifacts / "events.jsonl").read_text())

    def test_time_reserve_switches_to_reporting(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        # Run starts at zero; the first step starts inside the 20-second reserve.
        times = iter([0, 85, 85, 85, 85, 85, 85])
        with patch.object(corvee.time, "monotonic", side_effect=lambda: next(times, 85)), patch.object(client, "call", return_value=self.response("partial")) as call:
            self.assertEqual(corvee.run_steps(client, tools, [], "mock", None, 10, 100), 3)
        self.assertEqual(call.call_args.args[2]["tool_choice"], "none")
        self.assertLessEqual(client.timeout, 15)


class AuditRegressionTest(unittest.TestCase):
    def test_search_pattern_cannot_enable_preprocessor(self):
        if not shutil.which("rg"):
            self.skipTest("ripgrep unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("--pre=/usr/bin/printenv\n")
            tools = corvee.RepositoryTools(root, False, set())
            result = tools.tool_search_text("--pre=/usr/bin/printenv")
            self.assertIn("sample.txt:1:--pre=/usr/bin/printenv", result)

    def test_grep_fallback_filters_files_and_skips_symlinks(self):
        real_which = shutil.which
        if not real_which("grep"):
            self.skipTest("grep unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            (root / "sample.txt").write_text("--pre=marker\nalpha42\n")
            (root / "skip.py").write_text("alpha42\n")
            outside = Path(directory) / "outside.txt"
            outside.write_text("outside-secret\n")
            (root / "linked.txt").symlink_to(outside)
            tools = corvee.RepositoryTools(root, False, set())
            with patch.object(corvee.shutil, "which", side_effect=lambda name: None if name == "rg" else real_which(name)):
                result = tools.tool_search_text("alpha[0-9]+", "*.txt")
                self.assertIn("sample.txt:2:alpha42", result)
                self.assertNotIn("skip.py", result)
                self.assertIn("--pre=marker", tools.tool_search_text("--pre=marker"))
                self.assertEqual(tools.tool_search_text("outside-secret"), "")
                self.assertEqual(tools.tool_search_text("no-match"), "")
                self.assertFalse(json.loads(tools.execute("search_text", {"pattern": "["}))["ok"])

    def test_command_allowlist_pins_executable_and_rejects_path_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = corvee.RepositoryTools(root, False, {"echo"})
            (root / "echo").symlink_to(shutil.which("true"))
            for name in (str(root / "echo"), "./echo"):
                with self.assertRaises(ValueError):
                    tools.tool_run_command([name])
            with patch.dict(os.environ, {"PATH": str(root)}):
                self.assertIn("pinned-marker", tools.tool_run_command(["echo", "pinned-marker"]))
            with self.assertRaises(ValueError):
                tools.tool_run_command(["echo"], timeout_seconds=0)

    def test_model_selection_preserves_credentials_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            credentials = root / "custom.toml"
            credentials.write_text('api_key = "stored-key"\n')
            credentials.chmod(0o600)
            config.write_text('version = 1\nmodel = "old"\napi_key_env = "CUSTOM_KEY"\ncredentials_file = "custom.toml"\n')
            previous = credentials.read_bytes()
            with patch.dict(os.environ, {"CUSTOM_KEY": "temporary-key"}):
                corvee_config.update_selected_model(config, "new")
                settings = corvee_config.load_config(config)
                self.assertEqual(settings["model"], "new")
                self.assertEqual(settings["api_key_env"], "CUSTOM_KEY")
                self.assertEqual(settings["credentials_file"], "custom.toml")
                corvee_config.update_selected_model(config, None)
            self.assertEqual(corvee_config.load_config(config)["model"], "")
            self.assertEqual(credentials.read_bytes(), previous)
            self.assertFalse((root / "credentials.toml").exists())
            self.assertNotIn("temporary-key", config.read_text())

    def test_deadline_interrupts_blocked_provider(self):
        def blocked(*args):
            time.sleep(2)
            return {"choices": [{"message": {"content": "late report"}}]}
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        started = time.monotonic()
        with patch.object(client, "call", side_effect=blocked), self.assertRaises(SystemExit) as error:
            with corvee.execution_deadline(0.1):
                corvee.run_steps(client, tools, [], "mock", None, 2, 0.1)
        self.assertEqual(error.exception.code, 124)
        self.assertLess(time.monotonic() - started, 1)

    def test_deadline_stops_tool_batch_and_kills_command(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, {sys.executable})
        client = corvee.ApiClient("https://example.com", "fake")
        call = {"id": "1", "function": {"name": "run_command", "arguments": json.dumps({"argv": [sys.executable, "-c", "import time; time.sleep(5)"]})}}
        response = {"choices": [{"message": {"tool_calls": [call, call]}}]}
        started = time.monotonic()
        with patch.object(client, "call", return_value=response), patch.object(tools, "execute", wraps=tools.execute) as execute, self.assertRaises(SystemExit) as error:
            with corvee.execution_deadline(0.1):
                corvee.run_steps(client, tools, [], "mock", None, 2, 0.1)
        self.assertEqual(error.exception.code, 124)
        self.assertEqual(execute.call_count, 1)
        self.assertLess(time.monotonic() - started, 1)


if __name__ == "__main__":
    unittest.main()
