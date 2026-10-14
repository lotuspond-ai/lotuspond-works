import importlib
import json
import os

import pytest

from lotuspond_runtime.errors import FleetError


def setup(root, content=b"opaque-synthetic"):
    root.chmod(0o700)
    value = root / "value-v1"
    value.write_bytes(content)
    value.chmod(0o600)
    mapping = root / "mapping.json"
    mapping.write_text(
        json.dumps(
            {"bindings": [{"binding_id": "binding-a", "version": 1, "filename": "value-v1"}]}
        )
    )
    mapping.chmod(0o600)
    return value, mapping


def resolver(root, mapping):
    return importlib.import_module("lotuspond_runtime.resolver").MountedSecretResolver(
        root, mapping
    )


@pytest.mark.parametrize("size", [0, 1, 65536, 65537])
def test_resolver_race_and_bounds(tmp_path, size):
    _, mapping = setup(tmp_path, b"x" * size)
    r = resolver(tmp_path, mapping)
    if size > 65536:
        with pytest.raises(FleetError):
            r.resolve("binding-a", 1)
    else:
        assert r.resolve("binding-a", 1) == b"x" * size
    with pytest.raises(FleetError):
        r.resolve("binding-a", 2)
    with pytest.raises(FleetError):
        r.resolve("another-scope-binding", 1)


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "hardlink",
        "mode",
        "directory-mode",
        "mapping-mode",
        "traversal",
        "duplicate",
        "version-bool",
        "fifo",
    ],
)
def test_unsafe_storage(tmp_path, kind):
    value, mapping = setup(tmp_path)
    if kind == "symlink":
        value.rename(tmp_path / "other")
        value.symlink_to("other")
    elif kind == "hardlink":
        os.link(value, tmp_path / "other")
    elif kind == "mode":
        value.chmod(0o640)
    elif kind == "directory-mode":
        tmp_path.chmod(0o750)
    elif kind == "mapping-mode":
        mapping.chmod(0o644)
    elif kind == "fifo":
        value.unlink()
        os.mkfifo(value, 0o600)
    else:
        rows = [{"binding_id": "binding-a", "version": 1, "filename": "value-v1"}]
        if kind == "traversal":
            rows[0]["filename"] = "../value-v1"
        elif kind == "duplicate":
            rows *= 2
        else:
            rows[0]["version"] = True
        mapping.write_text(json.dumps({"bindings": rows}))
    with pytest.raises(FleetError):
        resolver(tmp_path, mapping).resolve("binding-a", 1)


@pytest.mark.parametrize("mutation", ["growth", "truncation", "replacement"])
def test_detects_mutation_during_read(tmp_path, monkeypatch, mutation):
    value, mapping = setup(tmp_path, b"x" * 65536)
    r = resolver(tmp_path, mapping)
    original = os.read
    changed = False

    def read(fd, size):
        nonlocal changed
        data = original(fd, size)
        if not changed and os.readlink(f"/proc/self/fd/{fd}") == str(value):
            changed = True
            if mutation == "growth":
                with value.open("ab") as f:
                    f.write(b"x")
            elif mutation == "truncation":
                value.write_bytes(b"x")
            else:
                value.unlink()
                value.write_bytes(b"y" * 65536)
                value.chmod(0o600)
        return data

    monkeypatch.setattr(os, "read", read)
    with pytest.raises(FleetError):
        r.resolve("binding-a", 1)
    assert changed


@pytest.mark.parametrize("component", ["root", "mapping", "nested-directory", "nested-file"])
def test_symlink_at_each_component(tmp_path, component):
    value, mapping = setup(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir(mode=0o700)
    if component == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        target = alias
    elif component == "mapping":
        alias = tmp_path / "alias"
        alias.symlink_to(mapping)
        mapping = alias
        target = tmp_path
    else:
        mapping.write_text(
            json.dumps(
                {
                    "bindings": [
                        {"binding_id": "binding-a", "version": 1, "filename": "nested/value"}
                    ]
                }
            )
        )
        if component == "nested-directory":
            nested.rmdir()
            nested.symlink_to(tmp_path, target_is_directory=True)
        else:
            (nested / "value").symlink_to(value)
        target = tmp_path
    with pytest.raises(FleetError):
        resolver(target, mapping).resolve("binding-a", 1)


def test_opened_file_owner_mismatch(tmp_path, monkeypatch):
    value, mapping = setup(tmp_path)
    fstat = os.fstat

    def owner(fd):
        info = fstat(fd)
        if os.readlink(f"/proc/self/fd/{fd}") == str(value):
            raw = list(info)
            raw[4] = os.geteuid() + 1
            return os.stat_result(raw)
        return info

    monkeypatch.setattr(os, "fstat", owner)
    with pytest.raises(FleetError):
        resolver(tmp_path, mapping).resolve("binding-a", 1)
