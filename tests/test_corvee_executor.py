import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from corvee_executor import ExecutorError, execute


class ExecutorTest(unittest.TestCase):
    def test_protocol_has_no_model_turn_and_preserves_command_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "fake-codex"
            server.write_text(f"#!{sys.executable}\n" + '''import json,sys
from pathlib import Path
requests=[]
for line in sys.stdin:
    msg=json.loads(line);requests.append(msg)
    Path('requests.json').write_text(json.dumps(requests))
    if msg['method']=='initialize':
        print(json.dumps({'id':msg['id'],'result':{}}),flush=True)
    elif msg['method']=='command/exec':
        print(json.dumps({'id':msg['id'],'result':{'exitCode':7,'stdout':'out','stderr':'err'}}),flush=True)
''')
            server.chmod(0o700)
            result = execute(str(server), ["trusted-gate", "argument with spaces"], root, 2)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "out", "err"))
            requests = json.loads((root / "requests.json").read_text())
            self.assertEqual([r["method"] for r in requests], ["initialize", "initialized", "command/exec"])
            params = requests[-1]["params"]
            self.assertEqual(params["command"], ["trusted-gate", "argument with spaces"])
            self.assertEqual(params["sandboxPolicy"], {"type": "workspaceWrite", "networkAccess": False})
            self.assertEqual(params["outputBytesCap"], 32768)

    def test_server_rejection_never_returns_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "fake-codex"
            server.write_text(f"#!{sys.executable}\n" + '''import json,sys
for line in sys.stdin:
    msg=json.loads(line)
    if 'id' in msg:
        print(json.dumps({'id':msg['id'],'error':{'code':-1,'message':'SECRET'}}),flush=True)
''')
            server.chmod(0o700)
            with self.assertRaisesRegex(ExecutorError, "Codex rejected") as caught:
                execute(str(server), ["must-not-run"], root, 2)
            self.assertNotIn("SECRET", str(caught.exception))
