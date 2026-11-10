from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)


def valid_id(value: str) -> str:
    return str(UUID(value))


Identifier = Annotated[str, AfterValidator(valid_id)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Json = dict[str, Any]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Register(Input):
    email: EmailStr
    name: Name
    password: str = Field(min_length=12, max_length=256)
    account_type: Literal["individual", "organization"] = "individual"
    workspace: Name | None = None
    invitation_token: str | None = Field(default=None, min_length=20, max_length=200)

    @model_validator(mode="after")
    def organization_name(self):
        if self.account_type == "organization" and not self.workspace:
            raise ValueError("An organization name is required")
        return self


class Login(Input):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class MfaEnrollmentStart(Input):
    current_password: str = Field(min_length=1, max_length=256)


class MfaEnrollmentConfirm(Input):
    code: str = Field(pattern=r"^[0-9]{6}$")


class MfaChallengeConfirm(Input):
    challenge_token: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=6, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class MfaManagement(Input):
    current_password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class TokenConfirm(Input):
    token: str = Field(min_length=20, max_length=200)


class PasswordResetRequest(Input):
    email: EmailStr


class PasswordResetConfirm(TokenConfirm):
    new_password: str = Field(min_length=12, max_length=256)


class InvitationInput(Input):
    email: EmailStr
    role: Literal["admin", "editor", "viewer"]
    days: int = Field(default=7, ge=1, le=30)


class Organization(Input):
    name: Name


class ProjectInput(Input):
    name: Name
    description: str = Field(default="", max_length=2000)


class EnvironmentInput(Input):
    project_id: Identifier
    name: Name
    kind: Literal["local", "sandbox", "production"]
    configuration: Json = Field(default_factory=dict)


class ScenarioInput(Input):
    project_id: Identifier
    name: Name
    modality: Literal["text", "voice", "tool", "multimodal"] = "text"
    input: Json
    expected: Json
    critical: bool = False


class RunnerInput(Input):
    name: Name
    capabilities: list[Name] = Field(default_factory=list, max_length=30)


class RunInput(Input):
    project_id: Identifier
    environment_id: Identifier | None = None
    name: Name
    revision: Name


class ResultInput(Input):
    scenario_id: Identifier
    passed: bool | None
    output: Json
    latency_ms: int = Field(ge=0, le=86400000)
    cost_usd: float = Field(default=0, ge=0, le=1000000)
    evidence: Json = Field(default_factory=dict)


class ResultBatch(Input):
    lease_token: str = Field(min_length=20, max_length=200)
    results: list[ResultInput] = Field(min_length=1, max_length=1000)


class LeaseInput(Input):
    lease_token: str = Field(min_length=20, max_length=200)


class PolicyInput(Input):
    name: Name
    minimum_pass_rate: Decimal = Field(
        default=Decimal(1), ge=0, le=1, max_digits=5, decimal_places=4
    )
    maximum_regression: Decimal = Field(
        default=Decimal(0), ge=0, le=1, max_digits=5, decimal_places=4
    )
    maximum_p95_ms: int | None = Field(default=None, gt=0)
    maximum_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=6)
    require_approval: bool = True


class ReleaseInput(Input):
    project_id: Identifier
    name: Name
    revision: Name
    candidate_run_id: Identifier
    baseline_run_id: Identifier | None = None
    policy_id: Identifier


class ApprovalInput(Input):
    release_id: Identifier | None = None
    action: Name
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalDecision(Input):
    status: Literal["approved", "rejected"]


class MemberInput(Input):
    email: EmailStr
    role: Literal["admin", "editor", "viewer"]


class KeyInput(Input):
    name: Name
    scopes: list[
        Literal["read", "write", "run", "execute", "execution_signal"]
    ] = Field(min_length=1, max_length=5)
    days: int = Field(default=90, ge=1, le=365)


class WorkspaceInput(Input):
    name: Name


class AuthenticatorReset(Input):
    current_password: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=10, max_length=2000)


class WorkspaceStatus(Input):
    status: Literal["active", "suspended"]
    reason: str = Field(min_length=10, max_length=2000)

