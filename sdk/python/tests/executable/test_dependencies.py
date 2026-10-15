"""Local closure admission, with independently assembled identities."""

from copy import deepcopy

import pytest

import lotuspond_sdk.executable as api
from lotuspond_sdk.executable import ExecutableError, executable_digest


def resolve(root, dependencies=None, catalog=None):
    assert hasattr(api, "resolve_dependencies"), "public closure resolver is missing"
    return api.resolve_dependencies(
        root,
        [] if dependencies is None else dependencies,
        {"documents": []} if catalog is None else catalog,
    )


def pin(package, name="child"):
    return {
        "id": name,
        "ref": {
            "kind": "workflow",
            "name": package["package_id"],
            "version": package["version"],
            "sha256": executable_digest(package),
        },
    }


def test_empty_closure(case):
    root = case("minimal")
    closure = resolve(root)
    assert closure.root.package_id == "synthetic-minimal"
    assert len(closure.packages) == 1
    assert closure.package_digests == {
        (
            "synthetic-minimal",
            "1.0.0",
        ): "724daea0bf5e9e1b724d74dc278b019a7edf1f1e3f98f76e223ca50e4fe71d57"
    }


def test_diamond_and_reordered_sources(case):
    leaf = case("minimal")
    left, right, root = [deepcopy(leaf) for _ in range(3)]
    for value, name in [(left, "left"), (right, "right"), (root, "root")]:
        value["package_id"] = name
    left["subworkflows"] = [pin(leaf)]
    right["subworkflows"] = [pin(leaf)]
    root["subworkflows"] = [pin(left, "left"), pin(right, "right")]
    first = resolve(root, [leaf, left, right, deepcopy(leaf)])
    second = resolve(root, [right, leaf, left])
    assert first.package_digests == second.package_digests
    assert len(first.packages) == 4


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing", "dependency_missing"),
        ("digest", "dependency_digest_mismatch"),
        ("conflict", "dependency_conflict"),
        ("unused", "dependency_conflict"),
    ],
)
def test_pinned_dependency_refusals(case, mutation, code):
    root, child = case("subworkflow"), case("minimal")
    deps = [child]
    if mutation == "missing":
        deps = []
    elif mutation == "digest":
        root["subworkflows"][0]["ref"]["sha256"] = "0" * 64
    elif mutation == "conflict":
        changed = deepcopy(child)
        changed["name"] = "changed"
        deps.append(changed)
    else:
        root = child
        deps = [case("decision")]
    with pytest.raises(ExecutableError) as error:
        resolve(root, deps)
    assert error.value.code == code


def test_raw_source_count_before_dedup(case):
    with pytest.raises(ExecutableError) as error:
        resolve(case("minimal"), [case("minimal")] * 64)
    assert error.value.code == "expansion_exceeded"


def test_complete_catalog_validation_before_dedup(case, catalog_case):
    catalog = catalog_case()
    doc = catalog["documents"][0]
    with pytest.raises(ExecutableError):
        resolve(case("minimal"), catalog={"documents": [doc] * 65})


def test_declared_unused_dependency_is_required(case):
    root = case("minimal")
    root["subworkflows"] = [pin(case("decision"))]
    with pytest.raises(ExecutableError) as error:
        resolve(root)
    assert error.value.code == "dependency_missing"


@pytest.mark.parametrize("depth,valid", [(8, True), (9, False)])
def test_dependency_depth_includes_root(case, depth, valid):
    packages = []
    for index in range(depth):
        value = case("minimal")
        value["package_id"] = f"level-{index}"
        if packages:
            value["subworkflows"] = [pin(packages[-1])]
        packages.append(value)
    if valid:
        assert len(resolve(packages[-1], packages[:-1]).packages) == depth
    else:
        with pytest.raises(ExecutableError) as error:
            resolve(packages[-1], packages[:-1])
        assert error.value.code == "expansion_exceeded"


def test_closure_64_includes_root(case):
    children = []
    root = case("minimal")
    for index in range(63):
        child = case("minimal")
        child["package_id"] = f"leaf-{index}"
        children.append(child)
    root["subworkflows"] = [pin(x, f"child-{i}") for i, x in enumerate(children)]
    assert len(resolve(root, children).packages) == 64


def test_cycle_diagnostic_before_inconsistent_hash(case):
    root = case("minimal")
    root["subworkflows"] = [
        {
            "id": "self",
            "ref": {
                "kind": "workflow",
                "name": root["package_id"],
                "version": root["version"],
                "sha256": "0" * 64,
            },
        }
    ]
    with pytest.raises(ExecutableError) as error:
        resolve(root)
    assert error.value.code == "dependency_cycle"


def test_unexpected_catalog_rejected(case, catalog_case):
    with pytest.raises(ExecutableError) as error:
        resolve(case("minimal"), catalog=catalog_case())
    assert error.value.code == "dependency_conflict"


def test_catalog_internal_pins_and_multiplicity(case, catalog_case):
    docs = catalog_case()["documents"]
    agent, model = docs[2], docs[1]
    root = case("minimal")
    root["agents"] = [
        {
            "id": "agent",
            "ref": {
                "kind": "agent",
                "name": agent["name"],
                "version": agent["version"],
                "sha256": api.catalog_document_digest(agent),
            },
        }
    ]
    with pytest.raises(ExecutableError) as error:
        resolve(root, catalog={"documents": [agent]})
    assert error.value.code == "dependency_missing"
    closure = resolve(root, catalog={"documents": [agent, model, deepcopy(model)]})
    assert len(closure.catalog) == 2


def test_mutated_package_is_fully_revalidated(case):
    root = api.validate_executable_package(case("minimal"))
    root.body.result.value = "not-null"
    with pytest.raises(ExecutableError):
        resolve(root)
