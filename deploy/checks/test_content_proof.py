import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "content_proof", Path(__file__).with_name("content-proof.py")
)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class TreeHashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative: str, content: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_hashes_path_and_bytes_in_sorted_order(self):
        self.write("b.py", b"second")
        self.write("a/c.py", b"first")
        expected = hashlib.sha256()
        for relative, content in ((b"a/c.py", b"first"), (b"b.py", b"second")):
            expected.update(relative)
            expected.update(b"\0")
            expected.update(content)
            expected.update(b"\0")
        result = proof.tree_hash(self.root)
        self.assertEqual(result, {"files": 2, "sha256": expected.hexdigest()})

    def test_a_renamed_file_changes_the_hash_even_with_identical_bytes(self):
        self.write("one.py", b"same")
        before = proof.tree_hash(self.root)["sha256"]
        (self.root / "one.py").rename(self.root / "two.py")
        self.assertNotEqual(proof.tree_hash(self.root)["sha256"], before)

    def test_dependency_build_and_cache_paths_are_not_source(self):
        self.write("kept.py", b"source")
        baseline = proof.tree_hash(self.root)
        for ignored in (
            "__pycache__/kept.cpython-314.pyc",
            "node_modules/pkg/index.js",
            ".venv/lib/thing.py",
            "dist/assets/index.js",
            "nested/__pycache__/other.pyc",
            "stray.pyc",
        ):
            self.write(ignored, b"not source")
        self.assertEqual(proof.tree_hash(self.root), baseline)

    def test_concatenation_cannot_be_forged_across_file_boundaries(self):
        # Without the NUL separators these two trees would hash identically.
        self.write("a", b"xy")
        self.write("b", b"z")
        first = proof.tree_hash(self.root)["sha256"]
        (self.root / "a").write_bytes(b"x")
        (self.root / "b").write_bytes(b"yz")
        self.assertNotEqual(proof.tree_hash(self.root)["sha256"], first)


if __name__ == "__main__":
    unittest.main()
