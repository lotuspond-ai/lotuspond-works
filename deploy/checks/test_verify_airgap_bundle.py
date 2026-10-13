import gzip
import hashlib
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import verify_airgap_bundle as verifier

ROOT = "lotuspond-airgap-1.2.3"
SCRIPT = Path(__file__).resolve().parents[1] / "airgap" / "verify-bundle.sh"


def manifest_for(files):
    return "".join(
        f"{hashlib.sha256(data).hexdigest()}  ./{name}\n"
        for name, data in sorted(files.items())
    ).encode()


class BundleFixture:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.archive = self.directory / "bundle.tar.gz"
        self.checksum = self.directory / "bundle.tar.gz.sha256"

    def write(self, members):
        with tarfile.open(self.archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
            for member, data in members:
                bundle.addfile(member, io.BytesIO(data) if member.isfile() else None)
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum.write_text(f"{digest}\n", encoding="ascii")

    def write_raw_tar(self, uncompressed):
        self.archive.write_bytes(gzip.compress(uncompressed))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksum.write_text(f"{digest}\n", encoding="ascii")

    @staticmethod
    def directory_member(name=ROOT):
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE
        member.mode = 0o755
        return member

    @staticmethod
    def file_member(name, data):
        member = tarfile.TarInfo(name)
        member.size = len(data)
        member.mode = 0o644
        return member

    def valid_members(self, files=None, manifest=None):
        files = files or {"INSTALL.md": b"review before loading\n", "images.tar": b"image bytes"}
        manifest = manifest if manifest is not None else manifest_for(files)
        members = [(self.directory_member(), b"")]
        members.extend((self.file_member(f"{ROOT}/{name}", data), data) for name, data in files.items())
        members.append((self.file_member(f"{ROOT}/MANIFEST.sha256", manifest), manifest))
        return members


class VerifyAirgapBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = BundleFixture(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def assert_rejected(self, message=None):
        with self.assertRaises(verifier.VerificationError) as raised:
            verifier.verify_bundle(self.fixture.archive, self.fixture.checksum)
        if message:
            self.assertIn(message, str(raised.exception))

    def test_valid_small_fixture_and_shell_entrypoint(self):
        self.fixture.write(self.fixture.valid_members())
        result = verifier.verify_bundle(self.fixture.archive, self.fixture.checksum)
        self.assertEqual(result.root, ROOT)
        self.assertEqual(result.regular_files, 3)

        completed = subprocess.run(
            [str(SCRIPT), str(self.fixture.archive), str(self.fixture.checksum)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("without extraction", completed.stdout)
        self.assertIn(f"root={ROOT}", completed.stdout)
        self.assertNotIn("lotuspond-airgap-verify.", completed.stdout)

    def test_tampered_file_with_matching_outer_checksum_is_rejected(self):
        original = {"INSTALL.md": b"approved\n"}
        stale_manifest = manifest_for(original)
        self.fixture.write(
            self.fixture.valid_members(files={"INSTALL.md": b"tampered\n"}, manifest=stale_manifest)
        )
        self.assert_rejected("manifest digest mismatch")

    def test_unmanifested_extra_file_is_rejected(self):
        listed = {"INSTALL.md": b"instructions\n"}
        actual = {**listed, "extra.txt": b"not listed\n"}
        self.fixture.write(self.fixture.valid_members(files=actual, manifest=manifest_for(listed)))
        self.assert_rejected("does not list every regular file")

    def test_symlink_followed_by_write_traversal_is_rejected(self):
        link = tarfile.TarInfo(f"{ROOT}/redirect")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        payload = b"would escape after unsafe extraction"
        members = [
            (self.fixture.directory_member(), b""),
            (link, b""),
            (self.fixture.file_member(f"{ROOT}/redirect/written.txt", payload), payload),
        ]
        self.fixture.write(members)
        self.assert_rejected("link or unsupported member type")

    def test_hardlink_and_device_members_are_rejected(self):
        for member_type in (tarfile.LNKTYPE, tarfile.CHRTYPE):
            with self.subTest(member_type=member_type):
                member = tarfile.TarInfo(f"{ROOT}/unsupported")
                member.type = member_type
                member.linkname = f"{ROOT}/INSTALL.md"
                self.fixture.write([(self.fixture.directory_member(), b""), (member, b"")])
                self.assert_rejected("link or unsupported member type")

    def test_duplicate_absolute_and_parent_member_paths_are_rejected(self):
        cases = []
        duplicate = self.fixture.file_member(f"{ROOT}/same.txt", b"one")
        duplicate_again = self.fixture.file_member(f"{ROOT}/same.txt", b"two")
        cases.append(
            (
                [(self.fixture.directory_member(), b""), (duplicate, b"one"), (duplicate_again, b"two")],
                "duplicate archive path",
            )
        )
        absolute = self.fixture.file_member("/absolute.txt", b"bad")
        cases.append(([(self.fixture.directory_member(), b""), (absolute, b"bad")], "unsafe archive path"))
        parent = self.fixture.file_member(f"{ROOT}/../outside.txt", b"bad")
        cases.append(([(self.fixture.directory_member(), b""), (parent, b"bad")], "unsafe archive path"))
        for members, message in cases:
            with self.subTest(message=message):
                self.fixture.write(members)
                self.assert_rejected(message)

    def test_manifest_cannot_name_parent_or_arbitrary_file(self):
        data = b"instructions\n"
        digest = hashlib.sha256(data).hexdigest()
        manifest = f"{digest}  ./../outside\n".encode()
        self.fixture.write(
            self.fixture.valid_members(files={"INSTALL.md": data}, manifest=manifest)
        )
        self.assert_rejected("unsafe archive path")

    def test_truncated_archive_is_rejected_even_with_updated_outer_checksum(self):
        self.fixture.write(self.fixture.valid_members())
        archive = self.fixture.archive.read_bytes()
        self.fixture.archive.write_bytes(archive[: len(archive) // 2])
        digest = hashlib.sha256(self.fixture.archive.read_bytes()).hexdigest()
        self.fixture.checksum.write_text(f"{digest}\n", encoding="ascii")
        self.assert_rejected("complete gzip USTAR archive")

    def test_compressed_and_decompressed_stream_limits_use_actual_reads(self):
        self.fixture.write(self.fixture.valid_members())
        compressed_size = self.fixture.archive.stat().st_size
        with mock.patch.object(verifier, "MAX_BUNDLE_BYTES", compressed_size - 1):
            self.assert_rejected("compressed-size limit")
        with mock.patch.object(verifier, "MAX_TAR_STREAM_BYTES", 1024):
            self.assert_rejected("decompressed tar-stream limit")

    def test_large_declared_pax_metadata_is_rejected_before_its_body_is_read(self):
        pax = tarfile.TarInfo("PaxHeader")
        pax.type = tarfile.XHDTYPE
        pax.size = 100_000_000
        self.fixture.write_raw_tar(pax.tobuf(format=tarfile.USTAR_FORMAT))
        self.assert_rejected("link or unsupported member type")

    def test_nonzero_trailing_tar_stream_is_rejected(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
            for member, data in self.fixture.valid_members():
                bundle.addfile(member, io.BytesIO(data) if member.isfile() else None)
        self.fixture.write_raw_tar(buffer.getvalue() + b"unexpected trailing data")
        self.assert_rejected("non-zero data after the USTAR end marker")


EXECUTION_ROOT = "lotuspond-execution-1.2.3"
EXECUTION_SCRIPT = Path(__file__).resolve().parents[1] / "execution" / "verify-bundle.sh"


class VerifyExecutionBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = BundleFixture(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def execution_members(self, files=None):
        files = files or {"INSTALL.md": b"review before loading\n", "images.tar": b"image bytes"}
        manifest = manifest_for(files)
        members = [(self.fixture.directory_member(EXECUTION_ROOT), b"")]
        members.extend(
            (self.fixture.file_member(f"{EXECUTION_ROOT}/{name}", data), data)
            for name, data in files.items()
        )
        members.append(
            (self.fixture.file_member(f"{EXECUTION_ROOT}/MANIFEST.sha256", manifest), manifest)
        )
        return members

    def test_execution_kind_accepts_execution_root_and_rejects_service_root(self):
        self.fixture.write(self.execution_members())
        result = verifier.verify_bundle(
            self.fixture.archive, self.fixture.checksum, kind="execution"
        )
        self.assertEqual(result.root, EXECUTION_ROOT)
        with self.assertRaises(verifier.VerificationError):
            verifier.verify_bundle(self.fixture.archive, self.fixture.checksum)
        completed = subprocess.run(
            [str(EXECUTION_SCRIPT), str(self.fixture.archive), str(self.fixture.checksum)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(f"root={EXECUTION_ROOT}", completed.stdout)

    def test_unsupported_kind_is_rejected_before_archive_inspection(self):
        self.fixture.write(self.execution_members())
        with self.assertRaisesRegex(verifier.VerificationError, "kind is not supported"):
            verifier.verify_bundle(
                self.fixture.archive, self.fixture.checksum, kind="unreviewed"
            )


if __name__ == "__main__":
    unittest.main()
