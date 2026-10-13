"""Pinned Gym-Anything catalog parsing and non-executing launch plans."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GYM_ANYTHING_REPOSITORY = "https://github.com/cmu-l3/gym-anything"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class GymAnythingEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: str
    cli_name: str
    path: str
    tasks: tuple[str, ...] = Field(min_length=1, max_length=5_000)

    @field_validator("environment_id", "cli_name")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        prefix = "benchmarks/cua_world/environments/"
        if not value.startswith(prefix) or value.startswith("/") or ".." in value.split("/"):
            raise ValueError("environment path must stay in the upstream benchmark catalog")
        if "\\" in value or value.endswith("/"):
            raise ValueError("environment path must be a normalized relative POSIX path")
        folder = value.removeprefix(prefix)
        if "/" in folder or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}_env", folder):
            raise ValueError("environment path must identify one reviewed environment folder")
        return value

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("task ids must be unique")
        if not all(_IDENTIFIER.fullmatch(task) for task in value):
            raise ValueError("task id contains unsupported characters")
        return value


class GymAnythingCatalog(BaseModel):
    """A Lotuspond-owned allowlist pinned to one reviewed upstream commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["lotuspond.gym-anything.catalog.v1"]
    repository: Literal[GYM_ANYTHING_REPOSITORY]
    revision: str
    environments: tuple[GymAnythingEnvironment, ...] = Field(min_length=1, max_length=1_000)

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _REVISION.fullmatch(value):
            raise ValueError("revision must be a full lowercase Git commit SHA")
        return value

    @model_validator(mode="after")
    def validate_unique_environments(self) -> "GymAnythingCatalog":
        ids = [entry.environment_id for entry in self.environments]
        cli_names = [entry.cli_name for entry in self.environments]
        paths = [entry.path for entry in self.environments]
        if (
            len(ids) != len(set(ids))
            or len(cli_names) != len(set(cli_names))
            or len(paths) != len(set(paths))
        ):
            raise ValueError("environment ids, CLI names, and paths must be unique")
        return self

    def environment(self, environment_id: str) -> GymAnythingEnvironment:
        for entry in self.environments:
            if entry.environment_id == environment_id:
                return entry
        raise ValueError("environment is not present in the reviewed catalog")


class GymAnythingLaunchSpec(BaseModel):
    """Validated argv metadata only. This object never downloads or executes code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: Literal[GYM_ANYTHING_REPOSITORY]
    revision: str
    environment_id: str
    cli_name: str
    environment_path: str
    task_id: str
    interactive: Literal[False] = False
    open_vnc: Literal[False] = False
    use_cache: bool = False
    cache_level: Literal["default"] = "default"

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if not _REVISION.fullmatch(value):
            raise ValueError("revision must be a full lowercase Git commit SHA")
        return value

    @field_validator("environment_id", "cli_name", "task_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("environment_path")
    @classmethod
    def validate_environment_path(cls, value: str) -> str:
        return GymAnythingEnvironment.validate_path(value)

    def argv(self) -> tuple[str, ...]:
        argv = ["gym-anything", "run", self.cli_name, "--task", self.task_id]
        if self.use_cache:
            argv.extend(("--use-cache", "--cache-level", self.cache_level))
        return tuple(argv)


def parse_gym_anything_catalog(data: object) -> GymAnythingCatalog:
    return GymAnythingCatalog.model_validate(data)


def build_gym_anything_launch_spec(
    catalog: GymAnythingCatalog,
    *,
    environment_id: str,
    task_id: str,
    use_cache: bool = False,
) -> GymAnythingLaunchSpec:
    environment = catalog.environment(environment_id)
    if task_id not in environment.tasks:
        raise ValueError("task is not present in the reviewed catalog")
    return GymAnythingLaunchSpec(
        repository=catalog.repository,
        revision=catalog.revision,
        environment_id=environment.environment_id,
        cli_name=environment.cli_name,
        environment_path=environment.path,
        task_id=task_id,
        use_cache=use_cache,
    )
