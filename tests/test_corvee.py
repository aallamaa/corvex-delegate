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
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from corvee_config import write_configuration
import corvee
import corvee_config
import install as skill_install
import configure_corvee
import io
from contextlib import contextmanager, redirect_stderr


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

    def test_resolves_credentials_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mission = Path(directory) / "mission.md"
            mission.write_text("inspect only", encoding="utf-8")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--no-config", "--model", "delegate-a",
                 "--mission", str(mission), "--cwd", directory, "--dry-run"],
                env=self.environment, text=True, capture_output=True, check=True,
            )
        self.assertIn('"api_key_present": true', result.stderr)
        self.assertIn(self.base_url, result.stderr)
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
            (Path(directory) / "mission.md").write_text("inspect only", encoding="utf-8")
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
                    "--model",
                    "delegate-a",
                    "--mission",
                    str(Path(directory) / "mission.md"),
                    "--cwd",
                    directory,
                    "--dry-run",
                ],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn('"api_key_present": true', result.stderr)
            self.assertIn(self.base_url, result.stderr)
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
                ["python3", str(CONFIGURE), "--config", str(config), "models"],
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
                ["python3", str(CONFIGURE), "--config", str(config), "select"],
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
                ["python3", str(CONFIGURE), "--config", str(config), "select"],
                env=clean_environment,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Model: not selected", shown_auto.stdout)

    def test_wrapper_run_accepts_timeout_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mission = root / "mission.md"
            mission.write_text("No-op.\n", encoding="utf-8")
            environment = {"CORVEX_API_KEY": "test-secret", "CORVEX_MODEL": "mock"}
            shared_timeout_result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--timeout",
                    "1s",
                    "run",
                    "--no-config",
                    "--mission",
                    str(mission),
                    "--cwd",
                    str(root),
                    "--model",
                    "mock",
                    "--dry-run",
                ],
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(shared_timeout_result.returncode, 0)
            self.assertNotIn("unknown option", shared_timeout_result.stderr + shared_timeout_result.stdout)
            self.assertIn('"http_timeout\": 1', shared_timeout_result.stdout + shared_timeout_result.stderr)

            post_command_timeout_result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "run",
                    "--timeout",
                    "1s",
                    "--no-config",
                    "--mission",
                    str(mission),
                    "--cwd",
                    str(root),
                    "--model",
                    "mock",
                    "--dry-run",
                ],
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(post_command_timeout_result.returncode, 0)
            self.assertNotIn("unrecognized arguments: --timeout 1s", post_command_timeout_result.stderr + post_command_timeout_result.stdout)
            self.assertNotIn("unknown option", post_command_timeout_result.stderr + post_command_timeout_result.stdout)
            self.assertIn('"http_timeout\": 1', post_command_timeout_result.stdout + post_command_timeout_result.stderr)

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
        # Wrap-up omits the schemas entirely; some servers emit tool calls
        # anyway when definitions are present with tool_choice "none".
        self.assertNotIn("tools", call.call_args.args[2])
        self.assertNotIn("tool_choice", call.call_args.args[2])

    def test_disabled_tools_are_not_executed_during_wrapup(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", return_value=self.response(calls=[self.tool_call()])), patch.object(tools, "execute") as execute:
            self.assertEqual(corvee.run_steps(client, tools, [], "mock", None, 1, 100), 3)
        execute.assert_not_called()

    def test_transient_retry_does_not_replay_completed_tool(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", side_effect=[self.response(calls=[self.tool_call()]), corvee_config.TransportError("request_timeout", True), self.response("done")]), patch.object(tools, "execute", return_value='{"ok":true,"result":"file"}') as execute, patch.object(corvee.time, "sleep"):
            self.assertEqual(corvee.run_steps(client, tools, [], "mock", None, 5, 100), 0)
        self.assertEqual(execute.call_count, 1)

    def test_retry_limit_and_permanent_failure(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        for retryable, expected_calls, code in [(True, 2, 75), (False, 1, 1)]:
            client = corvee.ApiClient("https://example.com", "fake")
            with patch.object(client, "call", side_effect=corvee_config.TransportError("failure", retryable)) as call, patch.object(corvee.time, "sleep"), self.assertRaises(SystemExit) as error:
                corvee.run_steps(client, tools, [], "mock", None, 5, 100)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(call.call_count, expected_calls)

    def test_socket_timeout_is_classified_without_traceback(self):
        # The transport lives in corvee_config, so the runner and the
        # configuration commands raise the same type from the same classifier.
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(corvee_config, "open_request", side_effect=TimeoutError("sensitive error")), self.assertRaises(corvee_config.TransportError) as error:
            client.call("POST", "/chat/completions", {})
        self.assertTrue(error.exception.retryable)
        self.assertEqual(str(error.exception), "request_timeout")
        self.assertNotIn("sensitive error", str(error.exception))

    def test_repeated_results_trigger_wrapup(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        with patch.object(client, "call", side_effect=[self.response(calls=[self.tool_call()])] * 3 + [self.response("partial")]) as call, patch.object(tools, "execute", return_value='{"ok":true,"result":"same"}') as execute:
            result = corvee.run_steps(client, tools, [], "mock", None, 20, 100)
        self.assertEqual(result, 3)
        self.assertEqual(execute.call_count, 3)
        # Wrap-up omits the schemas entirely; some servers emit tool calls
        # anyway when definitions are present with tool_choice "none".
        self.assertNotIn("tools", call.call_args.args[2])
        self.assertNotIn("tool_choice", call.call_args.args[2])

    def test_resume_checkpoint_trims_unmatched_tool_batch_and_preserves_context(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir()
            (run_dir / "checkpoint.json").write_text(json.dumps({
                "version": 1,
                "step": 2,
                "phase": "tool_batch",
                "run_context": {
                    "cwd": str(run_dir / "repo"),
                    "write": False,
                    "allowed_commands": ["echo"],
                },
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "run"},
                    {"role": "assistant", "tool_calls": [
                        {"id": "1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                        {"id": "2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    ]},
                    {"role": "tool", "tool_call_id": "1", "content": "{\"ok\":true}"},
                ],
            }, ensure_ascii=False))
            messages, next_step, phase, trimmed, context = corvee._load_checkpoint_messages(run_dir)
            self.assertTrue(trimmed)
            self.assertEqual(next_step, 3)
            self.assertEqual(phase, "tool_batch")
            self.assertEqual(messages, [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "run"},
            ])
            self.assertEqual(context["cwd"], str(run_dir / "repo"))
            self.assertFalse(context["write"])
            self.assertEqual(context["allowed_commands"], ["echo"])

    def test_resume_rejects_mismatched_run_context(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir()
            (run_dir / "checkpoint.json").write_text(json.dumps({
                "version": 1,
                "step": 1,
                "phase": "ready",
                "run_context": {
                    "cwd": str(Path(directory) / "original"),
                    "write": False,
                    "allowed_commands": ["echo"],
                },
                "messages": [{"role": "system", "content": "s"}],
            }, ensure_ascii=False))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--no-config",
                    "--resume",
                    str(run_dir),
                    "--cwd",
                    directory,
                    "--model",
                    "mock",
                    "--dry-run",
                ],
                env={"CORVEX_API_KEY": "test-secret", "CORVEX_MODEL": "mock"},
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("different --cwd", result.stderr + result.stdout)

    def test_resume_reloads_request_pending_step(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            run_dir.mkdir()
            checkpoint_path = run_dir / "checkpoint.json"
            checkpoint_path.write_text(json.dumps({
                "version": 1,
                "step": 5,
                "phase": "request_pending",
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ],
                "run_context": {
                    "cwd": str(Path(directory)),
                    "write": False,
                    "allowed_commands": [],
                },
            }, ensure_ascii=False))
            loaded_messages, next_step, phase, pending_trimmed, context = corvee._load_checkpoint_messages(run_dir)
            self.assertFalse(pending_trimmed)
            self.assertEqual(next_step, 5)
            self.assertEqual(phase, "request_pending")
            self.assertEqual(loaded_messages, [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
            ])
            self.assertEqual(context["cwd"], str(Path(directory)))

    def test_wrap_up_message_is_single_when_time_reserve_exhausted(self):
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        with patch.object(client, "call", return_value=None), patch.object(corvee.time, "monotonic", side_effect=lambda: 0.9), patch.object(corvee.time, "sleep"):
            with self.assertRaises(SystemExit) as error:
                corvee.run_steps(client, tools, messages, "mock", None, 3, 1)
        self.assertEqual(error.exception.code, 124)
        wraps = [
            message
            for message in messages
            if message.get("role") == "user" and "Execution stopped" in message.get("content", "")
        ]
        self.assertEqual(len(wraps), 1)

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
            with patch.object(sys, "argv", argv), patch.dict(os.environ, {"CORVEX_API_KEY": "fake-secret"}), patch.object(corvee.ApiClient, "call", side_effect=[self.response(calls=[self.tool_call()]), corvee_config.TransportError("request_timeout", True)]), patch.object(corvee.RepositoryTools, "execute", return_value='{"ok":true,"result":"saved evidence"}'), self.assertRaises(SystemExit) as error:
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
        # Wrap-up omits the schemas entirely; some servers emit tool calls
        # anyway when definitions are present with tool_choice "none".
        self.assertNotIn("tools", call.call_args.args[2])
        self.assertNotIn("tool_choice", call.call_args.args[2])
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


class CleanupTest(unittest.TestCase):
    def test_cleanup_older_than_days_and_dry_run_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory)
            now = time.time()
            old_dir = reports_root / uuid.uuid4().hex
            old_dir.mkdir()
            f1 = old_dir / "report.md"
            f1.touch()
            os.utime(f1, (now - 40 * 86400, now - 40 * 86400))
            os.utime(old_dir, (now - 40 * 86400, now - 40 * 86400))

            new_dir = reports_root / uuid.uuid4().hex
            new_dir.mkdir()
            f2 = new_dir / "report.md"
            f2.touch()
            os.utime(f2, (now - 5 * 86400, now - 5 * 86400))
            os.utime(new_dir, (now - 5 * 86400, now - 5 * 86400))

            dry_run_summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=True)
            self.assertEqual(dry_run_summary["removed"], 1)
            self.assertEqual(dry_run_summary["kept"], 1)
            self.assertEqual(dry_run_summary["skipped"], 0)
            self.assertEqual(dry_run_summary["failed"], 0)
            self.assertTrue(old_dir.exists())
            self.assertTrue(new_dir.exists())

            real_summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(real_summary["removed"], 1)
            self.assertEqual(real_summary["kept"], 1)
            self.assertEqual(real_summary["failed"], 0)
            self.assertFalse(old_dir.exists())
            self.assertTrue(new_dir.exists())

    def test_cleanup_skips_non_report_directory_without_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory)
            now = time.time()
            non_report = reports_root / "non_report_dir_uuid_12345"
            non_report.mkdir()
            f = non_report / "other_file.txt"
            f.write_text("data")
            os.utime(f, (now - 40 * 86400, now - 40 * 86400))
            os.utime(non_report, (now - 40 * 86400, now - 40 * 86400))

            summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(summary["removed"], 0)
            self.assertTrue(non_report.exists())

    def test_cleanup_deletes_marker_only_report_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory)
            now = time.time()
            marker_dir = reports_root / "custom_report_name"
            marker_dir.mkdir()
            f = marker_dir / "status.json"
            f.write_text('{"status":"ok"}')
            os.utime(f, (now - 40 * 86400, now - 40 * 86400))
            os.utime(marker_dir, (now - 40 * 86400, now - 40 * 86400))

            summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(summary["removed"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertFalse(marker_dir.exists())

    def test_cleanup_permission_restricted_read_only_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory)
            now = time.time()
            old_dir = reports_root / uuid.uuid4().hex
            old_dir.mkdir()
            marker = old_dir / "checkpoint.json"
            marker.write_text("{}")
            os.utime(marker, (now - 40 * 86400, now - 40 * 86400))
            protected = old_dir / "protected"
            protected.mkdir()
            protected_file = protected / "read_only.txt"
            protected_file.write_text("protected")
            os.utime(protected_file, (now - 40 * 86400, now - 40 * 86400))
            os.utime(protected, (now - 40 * 86400, now - 40 * 86400))
            os.chmod(protected, 0o100)
            os.utime(old_dir, (now - 40 * 86400, now - 40 * 86400))

            summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(summary["removed"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertFalse(old_dir.exists())

    def test_cleanup_symlink_target_permissions_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory) / "reports"
            reports_root.mkdir()
            external_root = Path(directory) / "external"
            external_root.mkdir()
            external_file = external_root / "outside.txt"
            external_file.write_text("outside", encoding="utf-8")
            external_file.chmod(0o400)
            external_mode = external_file.stat().st_mode & 0o777

            now = time.time()
            old_dir = reports_root / uuid.uuid4().hex
            old_dir.mkdir()
            marker = old_dir / "checkpoint.json"
            marker.write_text("{}")
            os.symlink(external_file, old_dir / "outside.txt")
            os.utime(marker, (now - 40 * 86400, now - 40 * 86400))
            os.utime(old_dir, (now - 40 * 86400, now - 40 * 86400))

            summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(summary["removed"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertFalse(old_dir.exists())
            self.assertTrue(external_file.exists())
            self.assertEqual(external_file.stat().st_mode & 0o777, external_mode)

    def test_cleanup_permission_restricted_read_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory)
            now = time.time()
            old_dir = reports_root / uuid.uuid4().hex
            old_dir.mkdir()
            f1 = old_dir / "checkpoint.json"
            f1.write_text("{}")
            os.utime(f1, (now - 40 * 86400, now - 40 * 86400))
            ro_file = old_dir / "read_only.txt"
            ro_file.write_text("protected")
            os.utime(ro_file, (now - 40 * 86400, now - 40 * 86400))
            os.chmod(ro_file, 0o400)
            os.utime(old_dir, (now - 40 * 86400, now - 40 * 86400))

            summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(summary["removed"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertFalse(old_dir.exists())

    def test_cleanup_reports_dir_tilde_expansion(self) -> None:
        temp_name = f".tmp_test_corvee_cleanup_{uuid.uuid4().hex}"
        home_temp = Path.home() / temp_name
        home_temp.mkdir(parents=True, exist_ok=True)
        try:
            old_dir = home_temp / uuid.uuid4().hex
            old_dir.mkdir()
            f = old_dir / "status.json"
            f.touch()
            now = time.time()
            os.utime(f, (now - 40 * 86400, now - 40 * 86400))
            os.utime(old_dir, (now - 40 * 86400, now - 40 * 86400))

            result = subprocess.run(
                [sys.executable, str(CLI), "cleanup", "--reports-dir", f"~/{temp_name}", "--older-than-days", "30"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(old_dir.exists())
        finally:
            shutil.rmtree(home_temp, ignore_errors=True)

    def test_cleanup_cli_supports_config_option(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory) / "reports"
            reports_root.mkdir()
            config_path = Path(directory) / "config.toml"
            config_path.write_text("model = 'mock'\n")
            old_dir = reports_root / uuid.uuid4().hex
            old_dir.mkdir()
            f1 = old_dir / "events.jsonl"
            f1.touch()
            now = time.time()
            os.utime(f1, (now - 40 * 86400, now - 40 * 86400))
            os.utime(old_dir, (now - 40 * 86400, now - 40 * 86400))

            res1 = subprocess.run(
                [sys.executable, str(CLI), "cleanup", "--config", str(config_path), "--reports-dir", str(reports_root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res1.returncode, 0)
            self.assertFalse(old_dir.exists())

            old_dir2 = reports_root / uuid.uuid4().hex
            old_dir2.mkdir()
            f2 = old_dir2 / "events.jsonl"
            f2.touch()
            os.utime(f2, (now - 40 * 86400, now - 40 * 86400))
            os.utime(old_dir2, (now - 40 * 86400, now - 40 * 86400))

            res2 = subprocess.run(
                [sys.executable, str(CLI), "--config", str(config_path), "cleanup", "--reports-dir", str(reports_root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res2.returncode, 0)
            self.assertFalse(old_dir2.exists())

            res3 = subprocess.run(
                [sys.executable, str(CLI), "--timeout", "1s", "cleanup",
                 "--reports-dir", str(reports_root)],
                capture_output=True,
                text=True,
            )
            # --timeout is treated as a shared option for cleanup in the wrapper.
            self.assertEqual(res3.returncode, 0)

            res4 = subprocess.run(
                [sys.executable, str(CLI), "cleanup", "--timeout", "1s",
                 "--reports-dir", str(reports_root)],
                capture_output=True,
                text=True,
            )
            # cleanup accepts timeout too, but treats it as a compatibility no-op.
            self.assertEqual(res4.returncode, 0)

    def test_cleanup_in_place_file_touch_refreshes_age(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reports_root = Path(directory)
            now = time.time()

            report_dir = reports_root / "run_touched"
            report_dir.mkdir()
            os.utime(report_dir, (now - 40 * 86400, now - 40 * 86400))

            inside_file = report_dir / "report.md"
            inside_file.write_text("updated", encoding="utf-8")
            os.utime(inside_file, (now - 2 * 86400, now - 2 * 86400))

            summary = corvee.cleanup_reports(reports_root, older_than_days=30, dry_run=False)
            self.assertEqual(summary["kept"], 1)
            self.assertEqual(summary["removed"], 0)
            self.assertTrue(report_dir.exists())

    def test_cleanup_non_directory_root_returns_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_root = Path(directory) / "not_a_dir.txt"
            file_root.write_text("invalid", encoding="utf-8")

            summary = corvee.cleanup_reports(file_root)
            self.assertGreater(summary["failed"], 0)

            result = subprocess.run(
                [sys.executable, str(CLI), "cleanup", "--reports-dir", str(file_root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)


class WriteProtectionTest(unittest.TestCase):
    """Writes must stay inside the repository and out of its control surfaces."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / ".git" / "hooks").mkdir(parents=True)
        (self.root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        (self.root / ".codex" / "corvee" / "reports" / "run").mkdir(parents=True)
        (self.root / ".codex" / "corvee" / "reports" / "run" / "report.md").write_text(
            "evidence", encoding="utf-8"
        )
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.tools = corvee.RepositoryTools(self.root, True, set())

    def test_write_file_rejects_git_directory(self) -> None:
        result = json.loads(self.tools.execute(
            "write_file", {"path": ".git/hooks/pre-commit", "content": "#!/bin/sh\ncurl evil\n"}
        ))
        self.assertFalse(result["ok"])
        self.assertIn("write-protected", result["error"])
        self.assertFalse((self.root / ".git" / "hooks" / "pre-commit").exists())

    def test_replace_text_rejects_git_config(self) -> None:
        result = json.loads(self.tools.execute(
            "replace_text", {"path": ".git/config", "old_text": "[core]", "new_text": "[core]\n\tsshCommand = evil"}
        ))
        self.assertFalse(result["ok"])
        self.assertIn("write-protected", result["error"])
        self.assertEqual((self.root / ".git" / "config").read_text(encoding="utf-8"), "[core]\n")

    def test_write_file_rejects_run_report_tree(self) -> None:
        result = json.loads(self.tools.execute(
            "write_file", {"path": ".codex/corvee/reports/run/report.md", "content": "all green"}
        ))
        self.assertFalse(result["ok"])
        self.assertEqual(
            (self.root / ".codex" / "corvee" / "reports" / "run" / "report.md").read_text(
                encoding="utf-8"
            ),
            "evidence",
        )

    def test_symlink_into_git_is_rejected_after_resolution(self) -> None:
        link = self.root / "innocent.txt"
        link.symlink_to(self.root / ".git" / "config")
        result = json.loads(self.tools.execute(
            "replace_text", {"path": "innocent.txt", "old_text": "[core]", "new_text": "owned"}
        ))
        self.assertFalse(result["ok"])
        self.assertIn("write-protected", result["error"])

    def test_ordinary_repository_writes_still_work(self) -> None:
        result = json.loads(self.tools.execute(
            "write_file", {"path": "src/new.py", "content": "ok\n"}
        ))
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.root / "src" / "new.py").read_text(encoding="utf-8"), "ok\n")

    def test_reads_of_protected_paths_remain_allowed(self) -> None:
        result = json.loads(self.tools.execute("read_file", {"path": ".git/config"}))
        self.assertTrue(result["ok"], result)


class CommandEnvironmentTest(unittest.TestCase):
    """run_command exposes an allow-list, not everything without a suspicious name."""

    def test_only_allow_listed_variables_reach_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tools = corvee.RepositoryTools(root, False, {"env"})
            leaky = {
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "AWS_SESSION": "leaked",
                "GH_ENTERPRISE_HOST": "internal.example",
                "CORVEX_API_URL": "https://provider.example/v1",
            }
            with patch.dict(os.environ, leaky):
                result = json.loads(tools.execute("run_command", {"argv": ["env"]}))
            self.assertTrue(result["ok"], result)
            output = result["result"]
            for name in leaky:
                self.assertNotIn(name, output)
            self.assertIn("PATH=", output)


class GrepFallbackTest(unittest.TestCase):
    """The no-ripgrep fallback must batch processes and still refuse to escape root."""

    def test_batched_search_finds_matches_without_ripgrep(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for index in range(12):
                (root / f"file{index}.txt").write_text(f"needle {index}\n", encoding="utf-8")
            (root / ".hidden.txt").write_text("needle hidden\n", encoding="utf-8")
            tools = corvee.RepositoryTools(root, False, set())
            real_which = shutil.which
            with patch.object(corvee.shutil, "which",
                              lambda name: None if name == "rg" else real_which(name)):
                with patch.object(corvee, "GREP_BATCH_SIZE", 5):
                    calls: list[list[str]] = []
                    real_run = corvee.run_process

                    def counting_run(argv, **kwargs):
                        calls.append(argv)
                        return real_run(argv, **kwargs)

                    with patch.object(corvee, "run_process", counting_run):
                        result = json.loads(tools.execute("search_text", {"pattern": "needle"}))
            self.assertTrue(result["ok"], result)
            for index in range(12):
                self.assertIn(f"file{index}.txt", result["result"])
            self.assertNotIn(".hidden.txt", result["result"])
            # 12 files at a batch size of 5 is three greps, not twelve.
            self.assertEqual(len(calls), 3)

    def test_symlink_outside_root_is_not_searched(self) -> None:
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as inside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("needle outside\n", encoding="utf-8")
            root = Path(inside).resolve()
            (root / "escape.txt").symlink_to(secret)
            (root / "real.txt").write_text("needle inside\n", encoding="utf-8")
            tools = corvee.RepositoryTools(root, False, set())
            real_which = shutil.which
            with patch.object(corvee.shutil, "which",
                              lambda name: None if name == "rg" else real_which(name)):
                result = json.loads(tools.execute("search_text", {"pattern": "needle"}))
            self.assertTrue(result["ok"], result)
            self.assertIn("real.txt", result["result"])
            self.assertNotIn("escape.txt", result["result"])


class DurationOptionTest(unittest.TestCase):
    """--timeout accepts the same duration syntax on every subcommand."""

    def test_configure_commands_accept_suffixed_durations(self) -> None:
        args = configure_corvee.parser().parse_args(["--timeout", "30m", "select"])
        self.assertEqual(args.timeout, 1800)

    def test_configure_commands_still_accept_plain_seconds(self) -> None:
        args = configure_corvee.parser().parse_args(["--timeout", "45", "select"])
        self.assertEqual(args.timeout, 45)

    def test_launcher_forwards_durations_to_check(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "check", "--timeout", "30m", "--config", "/nonexistent.toml"],
            capture_output=True, text=True,
        )
        self.assertNotIn("invalid int value", result.stderr)
        self.assertIn("configuration does not exist", result.stderr)

    def test_version_flag_reports_the_packaged_version(self) -> None:
        expected = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--version"], capture_output=True, text=True, check=True
        )
        self.assertIn(expected, result.stdout)


class PackageManifestTest(unittest.TestCase):
    """The package allow-list is hand-maintained; catch files nobody classified."""

    def test_every_tracked_resource_is_packaged_or_explicitly_excluded(self) -> None:
        import package

        known = set(package.PACKAGE_FILES) | set(package.TEST_FILES) | set(package.EXCLUDED_FILES)
        ignored_dirs = {".git", ".codex", "dist", "__pycache__", ".pytest_cache",
                        ".ruff_cache", ".mypy_cache"}
        unclassified = []
        for path in SKILL_ROOT.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(SKILL_ROOT)
            if ignored_dirs & set(relative.parts):
                continue
            if relative.suffix not in {".md", ".py", ".yaml", ".yml", ".toml", ""}:
                continue
            if str(relative) not in known:
                unclassified.append(str(relative))
        self.assertEqual(
            sorted(unclassified), [],
            "add these to PACKAGE_FILES, TEST_FILES, or EXCLUDED_FILES in scripts/package.py",
        )

    def test_packaged_resources_all_exist(self) -> None:
        import package

        self.assertTrue(list(package.checked_files(SKILL_ROOT, include_tests=True)))


class AdversarialHardeningTest(unittest.TestCase):
    """Regressions for bypasses found in an adversarial review of the write guard."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / "vendor" / "lib").mkdir(parents=True)
        (self.root / "src").mkdir()
        self.tools = corvee.RepositoryTools(self.root, True, set())

    def test_nested_repository_git_directory_is_protected(self) -> None:
        # Submodules and vendored checkouts put a real .git well below the root.
        result = json.loads(self.tools.execute(
            "write_file",
            {"path": "vendor/lib/.git/hooks/post-checkout", "content": "#!/bin/sh\npayload\n"},
        ))
        self.assertFalse(result["ok"])
        self.assertIn("write-protected", result["error"])

    def test_submodule_git_pointer_file_is_protected(self) -> None:
        result = json.loads(self.tools.execute(
            "write_file", {"path": "vendor/lib/.git", "content": "gitdir: /tmp/evil\n"}
        ))
        self.assertFalse(result["ok"])

    def test_case_variant_git_directory_is_protected(self) -> None:
        # APFS and NTFS resolve .GIT to the same directory as .git.
        for variant in (".GIT/hooks/pre-commit", ".Git/config", "vendor/.GiT/config"):
            with self.subTest(variant=variant):
                result = json.loads(self.tools.execute(
                    "write_file", {"path": variant, "content": "x"}
                ))
                self.assertFalse(result["ok"], variant)

    def test_directory_merely_named_git_is_writable(self) -> None:
        result = json.loads(self.tools.execute(
            "write_file", {"path": "src/git/helper.py", "content": "ok\n"}
        ))
        self.assertTrue(result["ok"], result)

    def test_git_configuration_flags_are_refused(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        tools = corvee.RepositoryTools(self.root, False, {"git"})
        for argv in (
            ["git", "-c", "core.pager=sh -c id", "status"],
            ["git", "--exec-path=/tmp/evil", "status"],
            ["git", "--config-env=core.pager=EVIL", "status"],
        ):
            with self.subTest(argv=argv):
                result = json.loads(tools.execute("run_command", {"argv": argv}))
                self.assertFalse(result["ok"], argv)
                self.assertIn("not permitted", result["error"])

    def test_ordinary_git_invocation_still_runs(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        tools = corvee.RepositoryTools(self.root, False, {"git"})
        result = json.loads(tools.execute("run_command", {"argv": ["git", "--version"]}))
        self.assertTrue(result["ok"], result)

    def test_pythonpath_is_not_inherited_by_commands(self) -> None:
        self.assertNotIn("PYTHONPATH", corvee.COMMAND_ENV_ALLOWLIST)

    def test_grep_fallback_reports_relative_paths(self) -> None:
        (self.root / "src" / "hit.txt").write_text("needle\n", encoding="utf-8")
        tools = corvee.RepositoryTools(self.root, False, set())
        real_which = shutil.which
        with patch.object(corvee.shutil, "which",
                          lambda name: None if name == "rg" else real_which(name)):
            result = json.loads(tools.execute("search_text", {"pattern": "needle"}))
        self.assertTrue(result["ok"], result)
        self.assertIn("src/hit.txt", result["result"])
        self.assertNotIn(str(self.root), result["result"])

    def test_duration_rejects_overflowing_and_non_ascii_values(self) -> None:
        import signal as signal_module

        import argparse as argparse_module

        for value in ("999999999h", "\u0663\u0660m", "0m", "+5", " 30m"):
            with self.subTest(value=value):
                with self.assertRaises(argparse_module.ArgumentTypeError):
                    corvee_config.parse_duration(value)
        # The accepted maximum must not overflow setitimer.
        accepted = corvee_config.parse_duration(f"{corvee_config.MAX_DURATION_SECONDS}")
        signal_module.setitimer(signal_module.ITIMER_REAL, accepted)
        signal_module.setitimer(signal_module.ITIMER_REAL, 0)


class CredentialRedirectionTest(unittest.TestCase):
    """A repo-local dotenv must not aim an externally supplied key at a new host."""

    def test_env_file_url_without_env_file_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("API_URL=https://attacker.example/v1\n", encoding="utf-8")
            mission = Path(directory) / "mission.md"
            mission.write_text("do nothing", encoding="utf-8")
            environment = {
                key: value for key, value in os.environ.items()
                if key not in {"CORVEX_API_URL", "CORVEX_MODEL"}
            } | {"CORVEX_API_KEY": "real-user-secret"}
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--no-config", "--model", "mock",
                 "--mission", str(mission), "--cwd", directory,
                 "--env-file", str(env_file), "--dry-run"],
                env=environment, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing", result.stderr)
            self.assertNotIn("attacker.example", result.stdout)

    def test_env_file_supplying_both_url_and_key_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "API_URL=https://provider.example/v1\nAPI_KEY=file-secret\n", encoding="utf-8"
            )
            mission = Path(directory) / "mission.md"
            mission.write_text("do nothing", encoding="utf-8")
            environment = {
                key: value for key, value in os.environ.items()
                if key not in {"CORVEX_API_KEY", "CORVEX_API_URL", "CORVEX_MODEL"}
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--no-config", "--model", "mock",
                 "--mission", str(mission), "--cwd", directory,
                 "--env-file", str(env_file), "--dry-run"],
                env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("provider.example", result.stderr)


class NativeAgentMarkerTest(unittest.TestCase):
    def test_inverted_provider_markers_raise_config_error(self) -> None:
        import native_agent

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                f"{native_agent.PROVIDER_END}\n{native_agent.PROVIDER_BEGIN}\n", encoding="utf-8"
            )
            with self.assertRaises(corvee_config.ConfigError):
                native_agent.install_native_agent(
                    codex_config=config,
                    agent_file=Path(directory) / "agent.toml",
                    credential_helper=Path(directory) / "helper.py",
                    delegate_config=config,
                    base_url="https://provider.example/v1",
                    model="mock",
                    reasoning_effort="medium",
                )


class WrapUpReserveTest(unittest.TestCase):
    """The time reserve exists to buy a partial report; a transient failure
    inside it must not discard that chance."""

    @staticmethod
    def response(text):
        return {"choices": [{"message": {"role": "assistant", "content": text}}]}

    def test_transient_failure_in_reserve_still_requests_a_report(self) -> None:
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")
        attempts = []

        def call(method, endpoint, payload):
            attempts.append(payload)
            if len(attempts) == 1:
                raise corvee_config.TransportError("http_503", True)
            return self.response("partial evidence")

        # Step one starts inside the reserve window and fails retryably.
        times = iter([0, 85, 85, 85, 85, 85, 85, 85, 85, 85])
        with patch.object(corvee.time, "monotonic", side_effect=lambda: next(times, 85)):
            with patch.object(client, "call", side_effect=call):
                code = corvee.run_steps(client, tools, [], "mock", None, 10, 100)
        self.assertEqual(code, 3)
        # A second request was made, and it asked for a report with no tools.
        self.assertEqual(len(attempts), 2)
        self.assertNotIn("tools", attempts[1])
        self.assertIn("Return a concise partial", json.dumps(attempts[1]["messages"]))

    def test_wrap_up_failure_after_announcement_reports_transient_failure(self) -> None:
        tools = corvee.RepositoryTools(Path.cwd(), False, set())
        client = corvee.ApiClient("https://example.com", "fake")

        def always_fail(method, endpoint, payload):
            raise corvee_config.TransportError("http_503", True)

        times = iter([0, 85, 85, 85, 85, 85, 85, 85, 85, 85, 85, 85])
        with patch.object(corvee.time, "monotonic", side_effect=lambda: next(times, 85)):
            with patch.object(client, "call", side_effect=always_fail):
                with self.assertRaises(SystemExit) as error:
                    corvee.run_steps(client, tools, [], "mock", None, 10, 100)
        # Retries are exhausted against a retryable status, which is exit 75,
        # not budget exhaustion. Either way no report is claimed.
        self.assertEqual(error.exception.code, 75)


class RunStateProtectionTest(unittest.TestCase):
    """Checkpoints hold repository content, so the default must be uncommitted."""

    def test_creating_a_run_directory_writes_a_codex_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / ".codex" / "corvee" / "reports" / "abc"
            corvee._protect_run_state(run_dir)
            marker = root / ".codex" / ".gitignore"
            self.assertTrue(marker.is_file())
            self.assertIn("corvee/reports/", marker.read_text(encoding="utf-8"))

    def test_an_existing_gitignore_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".codex").mkdir()
            marker = root / ".codex" / ".gitignore"
            marker.write_text("mine\n", encoding="utf-8")
            corvee._protect_run_state(root / ".codex" / "corvee" / "reports" / "abc")
            self.assertEqual(marker.read_text(encoding="utf-8"), "mine\n")

    def test_a_custom_run_dir_outside_codex_is_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corvee._protect_run_state(Path(directory) / "elsewhere" / "run")
            self.assertEqual(list(Path(directory).iterdir()), [])


class NativeAgentRemovalTest(unittest.TestCase):
    """install-agent edits the user's own Codex config; it needs a way back."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.config = self.home / "config.toml"
        self.agent = self.home / "agents" / "corvee.toml"
        self.agent.parent.mkdir(parents=True)

    def test_removal_strips_the_block_and_keeps_user_settings(self) -> None:
        import native_agent

        self.config.write_text(
            'model = "gpt-5"\n\n'
            f"{native_agent.PROVIDER_BEGIN}\n"
            '[model_providers.corvex]\nname = "Corvex"\n'
            f"{native_agent.PROVIDER_END}\n",
            encoding="utf-8",
        )
        self.agent.write_text('name = "corvee"\n', encoding="utf-8")
        message = native_agent.remove_native_agent(self.config, self.agent)
        remaining = self.config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', remaining)
        self.assertNotIn("corvex", remaining)
        self.assertFalse(self.agent.exists())
        self.assertIn("Removed", message)
        # What is left must still parse as TOML.
        import tomllib

        self.assertEqual(tomllib.loads(remaining), {"model": "gpt-5"})

    def test_removal_is_idempotent(self) -> None:
        import native_agent

        self.config.write_text('model = "gpt-5"\n', encoding="utf-8")
        message = native_agent.remove_native_agent(self.config, self.agent)
        self.assertIn("Nothing to remove", message)

    def test_install_then_remove_round_trips(self) -> None:
        import native_agent

        self.config.write_text('model = "gpt-5"\n', encoding="utf-8")
        native_agent.install_native_agent(
            codex_config=self.config, agent_file=self.agent,
            credential_helper=Path("scripts/credential_helper.py"),
            delegate_config=self.config, base_url="https://provider.example/v1",
            model="mock", reasoning_effort="medium",
        )
        self.assertIn("corvex", self.config.read_text(encoding="utf-8"))
        native_agent.remove_native_agent(self.config, self.agent)
        self.assertEqual(self.config.read_text(encoding="utf-8").strip(), 'model = "gpt-5"')


class SharedTransportTest(unittest.TestCase):
    """The runner and the configuration commands must classify provider
    failures identically, and neither may surface a response body."""

    @staticmethod
    def http_error(code):
        return corvee_config.error.HTTPError(
            "https://provider.example/v1/models", code, "boom", {},
            io.BytesIO(b'{"error":"Bearer sk-live-REALKEY is invalid"}'),
        )

    def test_retryable_statuses_agree_across_callers(self) -> None:
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                with patch.object(corvee_config, "open_request", side_effect=self.http_error(code)):
                    with self.assertRaises(corvee_config.TransportError) as error:
                        corvee_config.request_json(
                            corvee_config.build_provider_request(
                                "https://provider.example/v1", "/models", api_key="k"
                            ),
                            timeout=5,
                        )
                self.assertTrue(error.exception.retryable)
                self.assertEqual(error.exception.status, code)

    def test_client_errors_are_not_retried(self) -> None:
        for code in (400, 401, 403, 404, 422):
            with self.subTest(code=code):
                with patch.object(corvee_config, "open_request", side_effect=self.http_error(code)):
                    with self.assertRaises(corvee_config.TransportError) as error:
                        corvee_config.request_json(
                            corvee_config.build_provider_request(
                                "https://provider.example/v1", "/models", api_key="k"
                            ),
                            timeout=5,
                        )
                self.assertFalse(error.exception.retryable)

    def test_runner_and_configuration_classify_the_same_failure_alike(self) -> None:
        client = corvee.ApiClient("https://provider.example/v1", "k")
        with patch.object(corvee_config, "open_request", side_effect=self.http_error(503)):
            with self.assertRaises(corvee_config.TransportError) as runner_error:
                client.call("POST", "/chat/completions", {})
            with self.assertRaises(corvee_config.ConfigError) as config_error:
                corvee_config.fetch_models("https://provider.example/v1", "k", 5)
        self.assertEqual(runner_error.exception.category, "http_503")
        self.assertTrue(runner_error.exception.retryable)
        # Same underlying failure, audience-appropriate rendering.
        self.assertIn("HTTP 503", str(config_error.exception))

    def test_no_caller_leaks_the_response_body(self) -> None:
        client = corvee.ApiClient("https://provider.example/v1", "k")
        with patch.object(corvee_config, "open_request", side_effect=self.http_error(401)):
            with self.assertRaises(corvee_config.TransportError) as runner_error:
                client.call("GET", "/models")
            with self.assertRaises(corvee_config.ConfigError) as config_error:
                corvee_config.fetch_models("https://provider.example/v1", "k", 5)
            with self.assertRaises(corvee_config.ConfigError) as verify_error:
                corvee_config.verify_credential("https://provider.example/v1", "k", "m", 5)
        for surfaced in (runner_error, config_error, verify_error):
            self.assertNotIn("sk-live-REALKEY", str(surfaced.exception))

    def test_invalid_json_is_not_retryable(self) -> None:
        class Body:
            headers = None

            def read(self):
                return b"<html>not json</html>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.object(corvee_config, "open_request", return_value=Body()):
            with self.assertRaises(corvee_config.TransportError) as error:
                corvee_config.request_json(
                    corvee_config.build_provider_request(
                        "https://provider.example/v1", "/models", api_key="k"
                    ),
                    timeout=5,
                )
        self.assertEqual(error.exception.category, "invalid_json")
        self.assertFalse(error.exception.retryable)

    def test_runner_requests_do_not_arm_a_nested_deadline(self) -> None:
        # run_steps already holds the run-budget itimer; a second one would
        # fight it. Configuration commands, which hold none, must arm one.
        seen = []

        class Body:
            def read(self):
                return b'{"data":[]}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        original = corvee_config.request_deadline

        @contextmanager
        def spy(seconds):
            seen.append(seconds)
            with original(seconds):
                yield

        with patch.object(corvee_config, "open_request", return_value=Body()):
            with patch.object(corvee_config, "request_deadline", spy):
                corvee.ApiClient("https://provider.example/v1", "k", timeout=7).call("GET", "/models")
                self.assertEqual(seen, [])
                with self.assertRaises(corvee_config.ConfigError):
                    corvee_config.fetch_models("https://provider.example/v1", "k", 9)
                self.assertEqual(seen, [9])


if __name__ == "__main__":
    unittest.main()
