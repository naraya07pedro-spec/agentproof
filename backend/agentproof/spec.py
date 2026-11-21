"""Versioned test specifications for the deterministic fixture engine."""

import json
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Annotated, Literal, NoReturn, Self, cast

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from yaml.events import AliasEvent, CollectionStartEvent, ScalarEvent
from yaml.nodes import MappingNode, Node

ToolName = Literal["lookup", "record"]
Outcome = Literal[
    "completed", "denied", "timeout", "retry_exhausted", "malformed_response", "interrupted"
]
ProviderOutcome = Literal["ok", "timeout", "retryable_error", "malformed_response", "interrupted"]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Count = Annotated[int, Field(ge=0, le=16)]
MAX_SPEC_BYTES = 128 * 1024


def _validate_storage_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("text cannot contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text must be valid Unicode") from None
    return value


StorageText = Annotated[str, AfterValidator(_validate_storage_text)]
Text = Annotated[StorageText, Field(min_length=1, max_length=2048)]


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class CaseInput(SpecModel):
    text: Text = Field(repr=False)
    requested_tool: ToolName | None = None
    untrusted_note: Text | None = Field(default=None, repr=False)
    escalation_tool: ToolName | None = None
    repeat_tool_request: bool = False

    @model_validator(mode="after")
    def repeated_request_requires_tool(self) -> Self:
        if self.repeat_tool_request and self.requested_tool is None:
            raise ValueError("repeat_tool_request requires requested_tool")
        return self


class Expectation(SpecModel):
    outcome: Outcome
    output: Text | None = Field(default=None, repr=False)


class PolicyAssertions(SpecModel):
    denied_calls: Count | None = None
    duplicate_calls: Count | None = None
    applied_actions: Count | None = None
    honored_scope_requests: Count | None = None
    provider_requests: Count | None = None


class ProviderStep(SpecModel):
    outcome: ProviderOutcome
    delay_ms: int = Field(default=0, ge=0, le=60_000)


class CaseSpec(SpecModel):
    id: Identifier
    input: CaseInput
    expected: Expectation
    critical: bool = True
    tool_permissions: list[ToolName] = Field(default_factory=list, max_length=2)
    policy: PolicyAssertions = Field(default_factory=PolicyAssertions)
    provider: list[ProviderStep] = Field(
        default_factory=lambda: [ProviderStep(outcome="ok")], min_length=1, max_length=5
    )
    timeout_ms: int = Field(default=1_000, ge=1, le=60_000)
    max_attempts: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def tool_permissions_are_unique(self) -> Self:
        if len(set(self.tool_permissions)) != len(self.tool_permissions):
            raise ValueError("tool permissions must be unique")
        return self


class SuiteSpec(SpecModel):
    version: Literal["1"]
    name: Annotated[StorageText, Field(min_length=1, max_length=100)]
    cases: list[CaseSpec] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> Self:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case ids must be unique")
        return self


def _unique_mapping[Key: Hashable](pairs: list[tuple[Key, object]]) -> dict[Key, object]:
    result: dict[Key, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate specification key")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON numbers are unsupported")


class _SuiteLoader(yaml.SafeLoader):
    def compose_node(self, parent: Node | None, index: int) -> Node:
        event = cast(Callable[[], object], self.peek_event)()
        if isinstance(event, AliasEvent):
            raise ValueError("YAML aliases are unsupported")
        if isinstance(event, (ScalarEvent, CollectionStartEvent)) and event.tag is not None:
            raise ValueError("Explicit YAML tags are unsupported")
        return cast(Node, super().compose_node(parent, index))

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, object]:
        pairs: list[tuple[Hashable, object]] = []
        for key, value in node.value:
            constructed_key = self.construct_object(key, deep=deep)
            if not isinstance(constructed_key, str):
                raise ValueError("specification keys must be strings")
            pairs.append((constructed_key, self.construct_object(value, deep=deep)))
        return _unique_mapping(pairs)


def parse_suite_json(data: bytes | str) -> SuiteSpec:
    encoded = data.encode("utf-8") if isinstance(data, str) else data
    if len(encoded) > MAX_SPEC_BYTES:
        raise ValueError("specification exceeds 128 KiB")
    try:
        values = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_mapping,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid JSON specification") from None
    return SuiteSpec.model_validate(values)


def load_suite(path: Path) -> SuiteSpec:
    """Load a size-bounded UTF-8 JSON or YAML specification."""
    format_suffix = path.suffix.lower()
    if format_suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError("specification extension must be .json, .yaml or .yml")
    with path.open("rb") as stream:
        encoded = stream.read(MAX_SPEC_BYTES + 1)
    if format_suffix == ".json":
        return parse_suite_json(encoded)
    if len(encoded) > MAX_SPEC_BYTES:
        raise ValueError("specification exceeds 128 KiB")
    try:
        values = yaml.load(encoded.decode("utf-8"), Loader=_SuiteLoader)
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError):
        raise ValueError("invalid YAML specification") from None
    return SuiteSpec.model_validate(values)
