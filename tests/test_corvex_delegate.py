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

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from corvex_delegate_config import write_configuration


SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "corvex_delegate.py"
CONFIGURE = SKILL_ROOT / "scripts" / "configure_delegate.py"
INSTALL = SKILL_ROOT / "scripts" / "install.py"
CLI = SKILL_ROOT / "scripts" / "corvex-delegate"


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


class CorvexDelegateTest(unittest.TestCase):
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
            config = Path(directory) / "corvex-delegate" / "config.toml"
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
            target = codex_home / "skills" / "corvex-delegate"
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertFalse((target / ".env").exists())
            self.assertTrue((codex_home / "corvex-delegate" / "config.toml").is_file())
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
            self.assertFalse((bad_home / "skills" / "corvex-delegate").exists())
            self.assertFalse((bad_home / "corvex-delegate" / "config.toml").exists())
            self.assertNotIn("wrong-secret", rejected.stdout + rejected.stderr)

    def test_installs_native_provider_and_custom_agent_after_responses_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "corvex-delegate" / "config.toml"
            codex_config = root / "config.toml"
            agent_file = root / "agents" / "corvex_delegate.toml"
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
            self.assertIn('name = "corvex_delegate"', agent_text)
            self.assertIn('model = "delegate-a"', agent_text)
            self.assertNotIn("test-secret", provider_text + agent_text + installed.stdout)
            self.assertEqual(agent_file.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
