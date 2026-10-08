import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
