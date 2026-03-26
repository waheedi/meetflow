import tempfile
import unittest
from pathlib import Path

from app.services.source_resolver import SourceResolver, SourceResolverError


class TestSourceResolverHelpers(unittest.TestCase):
    def test_url_detection(self) -> None:
        self.assertTrue(SourceResolver._is_http_url("https://github.com/openai/openai-python"))
        self.assertFalse(SourceResolver._is_http_url("git@github.com:openai/openai-python.git"))
        self.assertFalse(SourceResolver._is_http_url("/tmp/local/path"))

    def test_git_source_detection(self) -> None:
        self.assertTrue(SourceResolver._looks_like_git_source("git@github.com:openai/openai-python.git"))
        self.assertTrue(SourceResolver._looks_like_git_source("https://github.com/openai/openai-python"))
        self.assertTrue(SourceResolver._looks_like_git_source("https://github.com/openai/openai-python.git"))
        self.assertFalse(SourceResolver._looks_like_git_source("/tmp/project"))

    def test_archive_name_and_suffix(self) -> None:
        self.assertTrue(SourceResolver._is_archive_name("repo.tar.gz"))
        self.assertTrue(SourceResolver._is_archive_name("repo.zip"))
        self.assertFalse(SourceResolver._is_archive_name("repo.git"))
        self.assertEqual(SourceResolver._archive_suffix("/foo/repo.tgz"), ".tgz")
        self.assertEqual(SourceResolver._archive_suffix("/foo/repo.unknown"), ".archive")

    def test_safe_destination_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            safe = SourceResolver._safe_destination(base, "dir/file.txt")
            self.assertTrue(str(safe).startswith(str(base.resolve())))
            with self.assertRaises(SourceResolverError):
                SourceResolver._safe_destination(base, "../evil.txt")

    def test_pick_root_dir_prefers_single_child_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            selected = SourceResolver._pick_root_dir(root)
            self.assertEqual(Path(selected).resolve(), (root / "repo").resolve())

    def test_pick_root_dir_keeps_extract_dir_with_multiple_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "b").mkdir()
            selected = SourceResolver._pick_root_dir(root)
            self.assertEqual(Path(selected).resolve(), root.resolve())


if __name__ == "__main__":
    unittest.main()
