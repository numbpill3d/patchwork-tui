import importlib.util
import io
from contextlib import redirect_stdout
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = pathlib.Path(__file__).parents[1] / "patchwork.py"
SPEC = importlib.util.spec_from_file_location("patchwork", MODULE_PATH)
patchwork = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patchwork)

class StageHunkTests(unittest.TestCase):
    def test_invalid_patch_does_not_stage_whole_file(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "Patchwork Test"], check=True)
            path = pathlib.Path(repo) / "sample.txt"
            path.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "-C", repo, "add", "sample.txt"], check=True)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
            path.write_text("after\n", encoding="utf-8")

            ok, error = patchwork.stage_hunk(
                repo, "sample.txt", "not a valid patch\n"
            )

            self.assertFalse(ok)
            self.assertIn("No valid patches", error)
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", repo, "diff", "--cached", "--quiet"]
                ).returncode,
                0,
            )


class PickRepoTests(unittest.TestCase):
    def test_print_mode_ui_can_be_kept_out_of_captured_path(self):
        ui = io.StringIO()
        captured = io.StringIO()
        with mock.patch.object(patchwork, "find_repos", return_value=["/tmp/repo"]), \
                mock.patch.object(patchwork, "repo_info", return_value=("main", 0)), \
                mock.patch.object(sys, "stdin", io.StringIO("1\n")), \
                redirect_stdout(captured):
            selected = patchwork.pick_repo(ui_stream=ui)
            print(selected)

        self.assertEqual(captured.getvalue(), "/tmp/repo\n")
        self.assertIn("/tmp/repo", ui.getvalue())
        self.assertIn("Select repo", ui.getvalue())


if __name__ == "__main__":
    unittest.main()
