"""Complete local definition admission. No lookup, I/O, or artifact execution."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .catalog import CatalogDocument, catalog_document_digest, validate_catalog
from .errors import ExecutableError
from .package import ExecutablePackage
from .validation import executable_digest, validate_executable_package

FAMILIES = (
    "models",
    "connectors",
    "agents",
    "subworkflows",
    "evaluations",
    "policies",
    "knowledge",
    "artifacts",
)


@dataclass(frozen=True)
class DependencyClosure:
    root: ExecutablePackage
    packages: Mapping[tuple[str, str], ExecutablePackage]
    package_digests: Mapping[tuple[str, str], str]
    catalog: Mapping[tuple[str, str, str], CatalogDocument]
    catalog_digests: Mapping[tuple[str, str, str], str]


@dataclass(frozen=True)
class _DependencyInventory:
    root: ExecutablePackage
    packages: Mapping[tuple[str, str], ExecutablePackage]
    package_digests: Mapping[tuple[str, str], str]
    catalog: Mapping[tuple[str, str, str], CatalogDocument]
    catalog_digests: Mapping[tuple[str, str, str], str]
    missing: frozenset[tuple[str, str, str]]


def _dependency_inventory(raw, dependencies, catalog) -> _DependencyInventory:
    """Admit every occurrence and audit all known edges, including incomplete inputs.

    Missing definitions cannot hide later supplied graph/pin errors. Supplied
    definitions require reachability through known edges; absent sources never
    justify otherwise unreachable extras. This is not a compiled/complete closure.
    """
    if type(dependencies) is not list:
        raise ExecutableError("invalid_package")
    if len(dependencies) > 63:
        raise ExecutableError("expansion_exceeded")
    # Each complete package admission enforces 1 MiB, so <=64 sources enforce 64 MiB.
    sources = [validate_executable_package(value) for value in [raw, *dependencies]]
    bundle = validate_catalog(catalog)
    packages, digests, documents, catalog_digests = {}, {}, {}, {}
    for package in sources:
        key = (package.package_id, package.version)
        digest = executable_digest(package)
        if key in digests and digests[key] != digest:
            raise ExecutableError("dependency_conflict")
        packages[key], digests[key] = package, digest
    for document in bundle.documents:
        key = (document.kind, document.name, document.version)
        documents[key], catalog_digests[key] = document, catalog_document_digest(document)
    root_key = (sources[0].package_id, sources[0].version)
    reached_packages, reached_catalog = set(), set()
    declared = {}
    missing = set()
    visited_depth = {}

    def check_pin(ref):
        key = (ref.kind, ref.name, ref.version)
        if key in declared and declared[key] != ref.sha256:
            raise ExecutableError("dependency_conflict")
        declared[key] = ref.sha256
        actual = digests.get(key[1:]) if ref.kind == "workflow" else catalog_digests.get(key)
        if actual is None:
            missing.add(key)
        elif actual != ref.sha256:
            raise ExecutableError("dependency_digest_mismatch")
        return key

    def visit_catalog(ref):
        key = check_pin(ref)
        if key in reached_catalog:
            return
        reached_catalog.add(key)
        if key not in documents:
            return
        definition = documents[key].definition
        if key[0] == "agent":
            visit_catalog(definition.model_ref)
        for tool in getattr(definition, "read_tools", []):
            visit_catalog(tool.connector_ref)

    def visit_package(key, active):
        if key in active:
            raise ExecutableError("dependency_cycle")
        if len(active) >= 8:
            raise ExecutableError("expansion_exceeded")
        reached_packages.add(key)
        if len(reached_packages) > 64:
            raise ExecutableError("expansion_exceeded")
        if key not in packages:
            return
        if visited_depth.get(key, -1) >= len(active):
            return
        visited_depth[key] = len(active)
        # Traverse all incoming paths: a shallow shared visit must not hide a deep path.
        for family in FAMILIES:
            for declaration in getattr(packages[key], family):
                if declaration.ref.kind == "workflow":
                    child = (declaration.ref.name, declaration.ref.version)
                    if child in (*active, key):
                        raise ExecutableError("dependency_cycle")
                    check_pin(declaration.ref)
                    visit_package(child, (*active, key))
                else:
                    visit_catalog(declaration.ref)

    visit_package(root_key, ())
    if packages.keys() - reached_packages or documents.keys() - reached_catalog:
        raise ExecutableError("dependency_conflict")
    return _DependencyInventory(
        sources[0],
        MappingProxyType(dict(sorted(packages.items()))),
        MappingProxyType(dict(sorted(digests.items()))),
        MappingProxyType(dict(sorted(documents.items()))),
        MappingProxyType(dict(sorted(catalog_digests.items()))),
        frozenset(missing),
    )


def resolve_dependencies(
    raw: object, dependencies: list[object], catalog: object
) -> DependencyClosure:
    """Resolve complete exact closure; incomplete inventories never escape as plans."""
    inventory = _dependency_inventory(raw, dependencies, catalog)
    if inventory.missing:
        raise ExecutableError("dependency_missing")
    return DependencyClosure(
        inventory.root,
        inventory.packages,
        inventory.package_digests,
        inventory.catalog,
        inventory.catalog_digests,
    )
