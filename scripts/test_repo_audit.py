"""仓库只读扫描工具的自动化测试。"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import repo_audit


class RepoAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git_add(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)

    def test_git_audit_flags_artifacts_duplicates_versions_and_layout(self):
        code = "def f(x):\n    return x + 1\n" * 10
        self.write("src/first.py", code)
        self.write("src/second.py", code)
        self.write("src/model_final_v2.py", "x = 1\n")
        self.write("outputs/model.pt", "fake model content\n")
        self.write("misc/helper.py", "value = True\n")
        self.git_add()
        report = repo_audit.audit(self.root, {"src", "outputs"}, 800)
        categories = {item["category"] for item in report["findings"]}
        self.assertEqual(report["source"], "git")
        self.assertIn("generated_artifact", categories)
        self.assertIn("duplicate_content", categories)
        self.assertIn("versioned_name", categories)
        self.assertIn("unexpected_top_level", categories)
        artifact = next(x for x in report["findings"] if x["category"] == "generated_artifact")
        self.assertEqual(artifact["priority"], "high")

    def test_non_git_scan_prunes_generated_directories(self):
        self.write("src/main.py", "ok = 1\n")
        self.write("datasets/image.py", "unused = 2\n")
        self.write(".venv/module.py", "unused = 3\n")
        self.write("outputs/train.log", "training...\n")
        report = repo_audit.audit(self.root, None, 800)
        self.assertEqual(report["source"], "filesystem")
        self.assertEqual(report["inspected_files"], 1)
        self.assertFalse(report["findings"])

    def test_scanner_does_not_modify_files(self):
        path = self.write("src/model_final.py", "value = 1\n")
        original = path.read_bytes()
        self.git_add()
        before = subprocess.check_output(["git", "-C", str(self.root), "status", "--porcelain"])
        repo_audit.audit(self.root, None, 800)
        after = subprocess.check_output(["git", "-C", str(self.root), "status", "--porcelain"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(after, before)

    def test_guard_exit_code_for_high_only(self):
        self.write("outputs/weights.pt", "model\n")
        self.git_add()
        script = Path(repo_audit.__file__)
        run = subprocess.run([sys.executable, str(script), "--root", str(self.root),
                              "--json", "--fail-on", "high"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        payload = json.loads(run.stdout)
        self.assertTrue(payload["findings"])

    def test_changed_scope_excludes_unchanged_legacy_files(self):
        self.write("src/legacy_final.py", "legacy = True\n")
        changed = self.write("src/train.py", "epoch = 1\n")
        self.git_add()
        subprocess.run(["git", "-C", str(self.root),
                        "-c", "user.name=Skill Test", "-c", "user.email=skill@example.test",
                        "commit", "-qm", "baseline"], check=True)
        changed.write_text("epoch = 2\n", encoding="utf-8")
        self.write("outputs/current.pt", "new checkpoint\n")
        report = repo_audit.audit(self.root, None, 800, "HEAD")
        self.assertEqual(report["source"], "git-changed")
        paths = [entry["path"] for entry in report["findings"]]
        self.assertNotIn("src/legacy_final.py", paths)
        self.assertIn("outputs/current.pt", paths)

    def test_chinese_cli_output_keeps_stable_json_fields(self):
        self.write("src/model_final.py", "value = 1\n")
        self.git_add()
        script = Path(repo_audit.__file__)
        cmd = [sys.executable, str(script), "--root", str(self.root)]
        text_result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertIn("只读体检", text_result.stdout)
        self.assertIn("疑似版本式命名", text_result.stdout)
        json_result = subprocess.run([*cmd, "--json"], capture_output=True, text=True, check=True)
        payload = json.loads(json_result.stdout)
        self.assertIn("versioned_name", [item["category"] for item in payload["findings"]])
        self.assertTrue(any("文件名带版本" in item["evidence"] for item in payload["findings"]))


if __name__ == "__main__":
    unittest.main()
