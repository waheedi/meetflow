import tempfile
import unittest
from pathlib import Path

from app.services.repository_analyzer import RepositoryAnalyzer


class TestRepositoryAnalyzer(unittest.TestCase):
    def test_analyze_detects_stack_and_evidence(self) -> None:
        analyzer = RepositoryAnalyzer(max_evidence_files=20)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text(
                "from fastapi import FastAPI\n\napp = FastAPI()\n\ndef run():\n    return 'ok'\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("# Demo\nThis is a demo repository.\n", encoding="utf-8")
            (root / "requirements.txt").write_text("fastapi==0.116.1\nuvicorn==0.35.0\n", encoding="utf-8")

            ctx = analyzer.analyze(str(root))

        self.assertEqual(Path(ctx.root_path), root.resolve())
        self.assertIn("Python", ctx.stack)
        self.assertIn("FastAPI", ctx.stack)
        self.assertIn("requirements.txt", ctx.manifests)
        self.assertTrue(any(item.path.endswith("src/main.py") for item in ctx.evidence))
        self.assertIn("src/", ctx.repo_tree)
        self.assertTrue(any("Indexed code files" in note for note in ctx.architecture_notes))

    def test_select_relevant_evidence_uses_query_terms(self) -> None:
        analyzer = RepositoryAnalyzer(max_evidence_files=10)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "api.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
            (root / "worker.py").write_text("def process_queue():\n    return 2\n", encoding="utf-8")
            ctx = analyzer.analyze(str(root))

        selected = analyzer.select_relevant_evidence(ctx, "handler api endpoint", limit=3)
        self.assertGreaterEqual(len(selected), 1)
        self.assertTrue(any("api.py" in item.path for item in selected))

    def test_get_path_context_for_file_and_missing_path(self) -> None:
        analyzer = RepositoryAnalyzer()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
            ctx = analyzer.analyze(str(root))

            found = analyzer.get_path_context(ctx, "service.py")
            missing = analyzer.get_path_context(ctx, "does/not/exist.py")

        self.assertIn("File context for `service.py`", found)
        self.assertIn("Requested path not found or not allowed", missing)


if __name__ == "__main__":
    unittest.main()
