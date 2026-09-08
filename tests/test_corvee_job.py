#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import corvee_job as cj  # noqa: E402


def usage(p, c):
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def impl_resp(content, p=10, c=5):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content) if isinstance(content, dict) else content}}],
        "usage": usage(p, c),
    }


def rev_resp(accepted, defects=None, p=7, c=3):
    return impl_resp(json.dumps({"accepted": accepted, "defects": defects or []}), p, c)


def make_provider(scripted):
    it = iter(scripted)

    def call(method, path, payload):
        return next(it)

    return call


class Tmp:
    def __init__(self):
        self.d = tempfile.TemporaryDirectory()
        # Provider edits must echo the canonical paths sent in source packets.
        # macOS temporary paths commonly enter through /var -> /private/var.
        self.root = Path(self.d.name).resolve()

    def write(self, name, body):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        return p


class TestCorveeJob(unittest.TestCase):
    def setUp(self):
        self.tmp = Tmp()
        self.addCleanup(self.tmp.d.cleanup)
        self.src = self.tmp.write("src/app.py", "def f():\n    return 1\n")
        self.prot = self.tmp.write("src/prot.txt", "PROT")
        self.mission = self.tmp.write("mission.txt", "make f return 2")
        self.gate = self.tmp.write("gate.py", "import sys\nexit(0)\n")

    def _job(self, call, **kw):
        return cj.Job(
            cwd=self.tmp.root,
            mission=self.mission.read_text(),
            model="m",
            scope=["src/app.py"],
            protected=["src/prot.txt"],
            gate_argv=[sys.executable, str(self.gate)],
            call=call,
            **kw,
        )

    def test_temporary_directory_alias_uses_canonical_provider_paths(self):
        from unittest.mock import patch

        real = self.tmp.root / "real-temp"
        real.mkdir()
        alias = self.tmp.root / "temp-alias"
        alias.symlink_to(real, target_is_directory=True)
        with patch.object(tempfile, "tempdir", str(alias)):
            fixture = Tmp()
        self.addCleanup(fixture.d.cleanup)
        self.assertEqual(fixture.root, fixture.root.resolve())
        src = fixture.write("app.py", "value = 1\n")
        job = cj.Job(
            cwd=alias / fixture.root.name, mission="set value to 2", model="m",
            scope=["app.py"], protected=[], gate_argv=[sys.executable, "-c", "pass"],
            call=make_provider([
                impl_resp({"edits": [{"path": str(src), "old": "value = 1", "new": "value = 2"}]}),
                rev_resp(True),
            ]),
        )
        result = job.run()
        self.assertEqual(result["status"], "ready", result["reason"])
        self.assertEqual(src.read_text(), "value = 2\n")

    def test_success(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            rev_resp(True),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "ready")
        self.assertEqual(self.src.read_text(), "def f():\n    return 2\n")
        self.assertEqual(r["aggregate_usage"]["prompt_tokens"], 17)
        self.assertEqual(r["aggregate_usage"]["completion_tokens"], 8)

    def test_gate_fail_then_repair(self):
        diag = "FAIL_DIAGNOSTIC_TEXT"
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            # gate fails first, so review happens on 2nd attempt, not here
            impl_resp({"edits": [{"path": str(self.src), "old": "return 2", "new": "return 2"}]}),
            rev_resp(True),
        ])
        bad_gate = self.tmp.write("badgate.py", f"import sys\nprint('{diag}')\nexit(1)\n")
        j = cj.Job(
            cwd=self.tmp.root,
            mission=self.mission.read_text(),
            model="m",
            scope=["src/app.py"],
            protected=[],
            gate_argv=[sys.executable, str(bad_gate)],
            call=prov,
            max_repairs=2,
        )
        captured = []
        orig_impl = cj.Job._implement

        def capture_impl(self, attempt, diagnostics):
            captured.append(diagnostics)
            return orig_impl(self, attempt, diagnostics)

        cj.Job._implement = capture_impl
        orig_gate = cj.Job._run_gate
        good_gate = self.gate
        attempts = {"i": 0}

        def gated(self, attempt):
            attempts["i"] += 1
            if attempts["i"] == 1:
                self.gate_argv = [sys.executable, str(bad_gate)]
            else:
                self.gate_argv = [sys.executable, str(good_gate)]
            return orig_gate(self, attempt)

        cj.Job._run_gate = gated
        try:
            r = j.run()
        finally:
            cj.Job._run_gate = orig_gate
            cj.Job._implement = orig_impl
        self.assertEqual(r["status"], "ready")
        # second implementer payload had actual failed gate diagnostic
        self.assertIn(diag, captured[1])
        self.assertEqual(len(r["phases"]), 3)

    def test_review_reject_repair(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 3"}]}),
            rev_resp(False, ["wrong value"]),
            impl_resp({"edits": [{"path": str(self.src), "old": "return 3", "new": "return 2"}]}),
            rev_resp(True),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "ready")
        self.assertEqual(self.src.read_text(), "def f():\n    return 2\n")

    def test_malformed_review_escalates(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            impl_resp("not json", 7, 3),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "escalated")
        self.assertTrue(any(p["usage"]["completion_tokens"] == 3 for p in r["phases"]))

    def test_out_of_scope_rejected_before_write(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.tmp.root / "other.py"), "old": "x", "new": "y"}]}),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(self.src.read_text(), "def f():\n    return 1\n")

    def test_ambiguous_edit_rejected(self):
        self.tmp.write("src/app.py", "x\nx\n")
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "x", "new": "y"}]}),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "escalated")

    def test_protected_modification_blocks(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
        ])
        j = self._job(prov)
        orig_rev = cj.Job._review

        def corr(self, attempt):
            self.protected[0].write_text("X")
            return orig_rev(self, attempt)

        cj.Job._review = corr
        try:
            r = j.run()
        finally:
            cj.Job._review = orig_rev
        self.assertEqual(r["status"], "escalated")

    def test_protected_gate_changed_blocks(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
        ])
        j = self._job(prov)
        orig_gate = cj.Job._run_gate

        def corr(self, attempt):
            self.protected[0].write_text("X")
            return orig_gate(self, attempt)

        cj.Job._run_gate = corr
        try:
            r = j.run()
        finally:
            cj.Job._run_gate = orig_gate
        self.assertEqual(r["status"], "escalated")

    def test_finish_reason_length_usage_retained(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}], "usage": usage(5, 9)},
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "escalated")
        self.assertTrue(any(p["usage"]["completion_tokens"] == 9 for p in r["phases"]))

    def test_max_repairs_zero(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 3"}]}),
            rev_resp(False, ["no"]),
        ])
        j = self._job(prov, max_repairs=0)
        r = j.run()
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(len(r["phases"]), 2)

    def test_cumulative_usage(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            rev_resp(True, p=20, c=10),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["aggregate_usage"], {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45})

    def test_path_resolution_success(self):
        rel = self.src.relative_to(self.tmp.root)
        p = cj._confined(str(rel), self.tmp.root)
        self.assertEqual(p.resolve(), self.src.resolve())

    def test_symlink_source_rejected(self):
        link = self.tmp.root / "link_app.py"
        os.symlink(self.src, link)
        with self.assertRaises(cj.JobError):
            cj._confined("link_app.py", self.tmp.root)

    def test_symlink_parent_rejected(self):
        linkdir = self.tmp.root / "linkdir"
        targetdir = self.tmp.root / "src"
        os.symlink(targetdir, linkdir)
        with self.assertRaises(cj.JobError):
            cj._confined("linkdir/app.py", self.tmp.root)

    def test_symlink_artifacts_rejected(self):
        target = self.tmp.root / "outside"
        target.mkdir()
        os.symlink(target, self.tmp.root / ".codex")
        with self.assertRaises(cj.JobError):
            self._job(make_provider([]))

    def test_multiple_invalid_edits_no_write(self):
        prov = make_provider([
            impl_resp({"edits": [
                {"path": str(self.src), "old": "return 1", "new": "return 2"},
                {"path": str(self.src), "old": "nonexistent", "new": "x"},
            ]}),
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(self.src.read_text(), "def f():\n    return 1\n")

    def test_source_change_during_provider_call_rejected(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            rev_resp(True),
        ])
        j = self._job(prov)
        orig_call = cj.Job._request

        def modify_during_call(self, system, user, phase, attempt):
            if phase == "implement":
                self.scope[0].write_text("CHANGED BY EXTERNAL")
            return orig_call(self, system, user, phase, attempt)

        cj.Job._request = modify_during_call
        try:
            r = j.run()
        finally:
            cj.Job._request = orig_call
        self.assertEqual(r["status"], "escalated")

    def test_reviewer_does_not_mutate(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            rev_resp(True),
        ])
        j = self._job(prov)
        orig_rev = cj.Job._review

        def no_mutate(self, attempt):
            self.scope[0].write_text("REVIEWER MUTATED")
            return orig_rev(self, attempt)

        cj.Job._review = no_mutate
        try:
            r = j.run()
        finally:
            cj.Job._review = orig_rev
        self.assertEqual(r["status"], "escalated")

    def test_missing_usage_escalates_and_retains(self):
        prov = make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"accepted": True, "defects": []})}}], "usage": None},
        ])
        j = self._job(prov)
        r = j.run()
        self.assertEqual(r["status"], "escalated")
        self.assertEqual(len(r["phases"]), 2)
        self.assertEqual(r["phases"][1]["phase"], "review")
        self.assertEqual(r["aggregate_usage"]["completion_tokens"], 5)

    def test_no_retries_under_max_repairs_2(self):
        scripted = [
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}),
            rev_resp(True),
        ]
        prov = make_provider(scripted)
        j = self._job(prov, max_repairs=2)
        r = j.run()
        self.assertEqual(r["status"], "ready")
        self.assertEqual(j._calls, 2)
        self.assertEqual(j._max_calls, 6)

    def test_fresh_packets_include_tests_and_no_tools(self):
        payloads = []
        responses = iter([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 3"}]}),
            rev_resp(False, ["return 2 instead"]),
            impl_resp({"edits": [{"path": str(self.src), "old": "return 3", "new": "return 2"}]}),
            rev_resp(True),
        ])
        def call(method, endpoint, payload):
            payloads.append(payload)
            return next(responses)
        result = self._job(call).run()
        self.assertEqual(result["status"], "ready")
        for payload in payloads:
            self.assertNotIn("tools", payload)
            self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])
        repair = json.loads(payloads[2]["messages"][1]["content"])
        self.assertIn("return 3", repair["files"][str(self.src)])
        self.assertIn("return 2 instead", repair["diagnostics"])
        self.assertIn(str(self.prot), repair["protected_files_read_only"])
        review = json.loads(payloads[3]["messages"][1]["content"])
        self.assertIn("return 1", review["baseline_files"][str(self.src)])
        self.assertIn("return 2", review["current_files"][str(self.src)])

    def test_oversized_packet_no_call(self):
        calls = []
        result = self._job(lambda *args: calls.append(args), max_input_bytes=5).run()
        self.assertEqual(result["status"], "escalated")
        self.assertEqual(calls, [])

    def test_partial_usage_is_not_zero_cost_success(self):
        response = impl_resp({"edits": []})
        response["usage"] = {"prompt_tokens": 17, "completion_tokens": -1}
        result = self._job(make_provider([response])).run()
        self.assertEqual(result["status"], "escalated")
        self.assertFalse(result["accounting_complete"])
        self.assertEqual(result["aggregate_usage"]["prompt_tokens"], 17)

    def test_transport_error_does_not_leak(self):
        def call(*args):
            raise RuntimeError("SECRET_SENTINEL")
        result = self._job(call).run()
        self.assertFalse(result["accounting_complete"])
        self.assertNotIn("SECRET_SENTINEL", json.dumps(result))
        self.assertEqual(len(result["phases"]), 1)

    def test_private_logs_and_mode_preserved(self):
        self.src.chmod(0o640)
        result = self._job(make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]}), rev_resp(True)
        ])).run()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.src.stat().st_mode & 0o777, 0o640)
        job_dir = Path(result["job_dir"])
        self.assertEqual(job_dir.stat().st_mode & 0o777, 0o700)
        for name in ("result.json", "gate-0.log"):
            self.assertEqual((job_dir/name).stat().st_mode & 0o777, 0o600)

    def test_gate_drift_prevents_cheap_review(self):
        self.gate.write_text("from pathlib import Path; Path('src/app.py').write_text('drift')")
        result = self._job(make_provider([
            impl_resp({"edits": [{"path": str(self.src), "old": "return 1", "new": "return 2"}]})
        ])).run()
        self.assertEqual(result["status"], "escalated")
        self.assertIn("scope changed", result["reason"])
        self.assertEqual(len(result["phases"]), 1)

    def test_rejected_review_evidence_survives_escalation(self):
        result = self._job(make_provider([
            impl_resp({"edits": []}), rev_resp(False, ["Missing required behavior"])
        ]), max_repairs=0).run()
        disk = json.loads((Path(result["job_dir"])/"result.json").read_text())
        self.assertEqual(disk["status"], "escalated")
        self.assertEqual(disk["model"], "m")
        self.assertEqual(disk["reviews"], [{"attempt": 0, "accepted": False, "defects": ["Missing required behavior"]}])
        self.assertIn("Missing required behavior", disk["last_diagnostics"])

    def test_kimi_uses_verified_thinking_key(self):
        payloads = []
        responses = iter([impl_resp({"edits": []}), rev_resp(True)])
        def call(method, endpoint, payload):
            payloads.append(payload)
            return next(responses)
        job = self._job(call, thinking="disabled")
        job.model = "moonshotai/Kimi-K2.7-Code"
        self.assertEqual(job.run()["status"], "ready")
        self.assertTrue(all(p["chat_template_kwargs"] == {"thinking": False} for p in payloads))
        self.assertEqual(cj.thinking_parameters("zai-org/GLM-5.2-FP8", "disabled"), {"enable_thinking": False})

    def test_codex_gate_failure_does_not_fall_back(self):
        from unittest.mock import patch
        from corvee_executor import ExecutorError
        provider = make_provider([impl_resp({"edits": []})])
        with patch("corvee_executor.execute", side_effect=ExecutorError("failed")) as executor:
            result = self._job(provider, executor="codex").run()
        self.assertEqual(result["status"], "escalated")
        self.assertIn("no local fallback", result["reason"])
        executor.assert_called_once()
        self.assertEqual(result["gates"], [])
        self.assertEqual(len(result["phases"]), 1)


if __name__ == "__main__":
    unittest.main()
