import hashlib
import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
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


class SdkProofTests(unittest.TestCase):
    def test_changing_only_sdk_source_breaks_match(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for path, data in {
                "backend/lotuspond/__init__.py": b"backend",
                "web/index.ts": b"web",
                "scripts/run.mjs": b"scripts",
                "dist/index.html": b"ui",
                "sdk/python/src/lotuspond_sdk/__init__.py": b"sdk",
                "runtime/python/src/lotuspond_runtime/__init__.py": b"runtime",
                "sdk/python/src/lotuspond_sdk/py.typed": b"",
                "sdk/python/src/lotuspond_sdk/executable/compiler.py": b"nested compiler",
                "bundle": b"bundle", "smoke": b"smoke",
            }.items():
                full = repo / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(data)
            sdk = repo / "sdk/python/src/lotuspond_sdk"
            embedded = {
                "/app/backend/lotuspond": proof.tree_hash(repo / "backend/lotuspond"),
                "/app/dist": proof.tree_hash(repo / "dist"),
                "lotuspond_sdk": proof.tree_hash(sdk),
                "lotuspond_runtime": proof.tree_hash(repo / "runtime/python/src/lotuspond_runtime"),
            }
            args = SimpleNamespace(bundle=repo / "bundle", smoke_report=repo / "smoke",
                                   api_image="api")
            with patch.object(proof, "image_id", return_value="sha256:synthetic"), patch.object(
                proof, "embedded_tree", side_effect=lambda image, root: embedded[root]
            ):
                self.assertTrue(all(proof.build(repo, args)["embedded_matches_source"].values()))
                (sdk / "executable/compiler.py").write_bytes(b"changed nested sdk only")
                matches = proof.build(repo, args)["embedded_matches_source"]
                self.assertFalse(matches["sdk_runtime_module"])
                self.assertTrue(matches["backend_runtime_module"])
                self.assertTrue(matches["static_ui"])


if __name__ == "__main__":
    unittest.main()


def test_runtime_distribution_is_part_of_api_image_content_proof(tmp_path):
    """Changing only the new backend distribution must invalidate image identity."""
    import argparse

    for relative in ("backend/lotuspond/__init__.py", "web/index.ts", "dist/index.html",
                     "sdk/python/src/lotuspond_sdk/__init__.py",
                     "runtime/python/src/lotuspond_runtime/__init__.py", "bundle", "smoke"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    embedded = {"/app/backend/lotuspond": proof.tree_hash(tmp_path / "backend/lotuspond"),
                "/app/dist": proof.tree_hash(tmp_path / "dist"),
                "lotuspond_sdk": proof.tree_hash(tmp_path / "sdk/python/src/lotuspond_sdk"),
                "lotuspond_runtime": proof.tree_hash(tmp_path / "runtime/python/src/lotuspond_runtime")}
    args = argparse.Namespace(bundle=tmp_path / "bundle", smoke_report=tmp_path / "smoke",
                              api_image="api")
    with patch.object(proof, "image_id", return_value="sha256:synthetic"), patch.object(
            proof, "embedded_tree", side_effect=lambda image, root: embedded[root]):
        first = proof.build(tmp_path, args)
        assert first["embedded_matches_source"].get("fleet_runtime_module") is True
        (tmp_path / "runtime/python/src/lotuspond_runtime/__init__.py").write_text("changed")
        second = proof.build(tmp_path, args)
        assert second["embedded_matches_source"]["fleet_runtime_module"] is False
        assert second["embedded_matches_source"]["sdk_runtime_module"] is True
