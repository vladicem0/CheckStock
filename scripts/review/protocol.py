"""Fail-closed JSON validation shared by the review launcher and runner.

The bundled schema intentionally uses a small, documented JSON Schema subset.
Unsupported keywords are rejected, rather than silently ignored. No third-party
packages or free-text verdict extraction are required.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("review.schema.json")
ZERO_SHA = "0" * 40
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SCHEMA_KEYWORDS = frozenset({
    "$schema", "title", "description", "type", "properties", "required",
    "additionalProperties", "items", "enum", "pattern", "minimum", "minLength",
})
_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def _json_value(value: Any, path: str = "$", depth: int = 0) -> None:
    """Reject Python-only values and non-finite numbers, including in metadata."""
    if depth > 100:
        raise ValueError(f"{path}: JSON nesting exceeds the supported limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: object keys must be strings")
            _json_value(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]", depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite numbers are not JSON values")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ValueError(f"{path}: value is not JSON-compatible")


def _check_schema(schema: dict[str, Any], path: str = "schema") -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: schema must be an object")
    unknown = set(schema) - SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"{path}: unsupported schema keywords: {sorted(unknown)}")
    kind = schema.get("type")
    if kind is not None and (not isinstance(kind, str) or kind not in _TYPE_CHECKS):
        raise ValueError(f"{path}: unsupported type {kind!r}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"{path}: properties must be an object")
    for key, subschema in properties.items():
        if not isinstance(key, str):
            raise ValueError(f"{path}: property names must be strings")
        _check_schema(subschema, f"{path}.properties.{key}")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
        raise ValueError(f"{path}: required must be an array of property names")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise ValueError(f"{path}: only boolean additionalProperties is supported")
    if "items" in schema:
        _check_schema(schema["items"], f"{path}.items")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError(f"{path}: enum must be a nonempty array")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as exc:
            raise ValueError(f"{path}: invalid pattern") from exc
    if "minimum" in schema and (
        isinstance(schema["minimum"], bool)
        or not isinstance(schema["minimum"], (int, float))
        or not math.isfinite(schema["minimum"])
    ):
        raise ValueError(f"{path}: minimum must be a finite number")
    if "minLength" in schema and (
        isinstance(schema["minLength"], bool)
        or not isinstance(schema["minLength"], int)
        or schema["minLength"] < 0
    ):
        raise ValueError(f"{path}: minLength must be a nonnegative integer")


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    kind = schema.get("type")
    if kind is not None and not _TYPE_CHECKS[kind](value):
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        raise ValueError(f"{path}: value is not in the allowed enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"{path}: missing required properties: {sorted(missing)}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise ValueError(f"{path}: unexpected properties: {sorted(extra)}")
        for key, subschema in properties.items():
            if key in value:
                _validate(value[key], subschema, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: string is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path}: string does not match the required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            raise ValueError(f"{path}: value is below minimum")


def validate_schema(value: Any, schema: dict[str, Any]) -> None:
    """Validate the explicit schema subset, rejecting unsupported keywords."""
    _check_schema(schema)
    _json_value(value)
    _validate(value, schema, "$")


def load_schema() -> dict[str, Any]:
    schema = strict_json_loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    _check_schema(schema)
    return schema


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def strict_json_loads(raw: str) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Expected a nonempty JSON document")
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (json.JSONDecodeError, TypeError, RecursionError) as exc:
        raise ValueError(f"Invalid JSON document: {exc}") from exc


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_PATTERN.fullmatch(value) is not None


def validate_result(result: Any, base_sha: str, head_sha: str) -> dict[str, Any]:
    """Validate structure, requested identities, and verdict consistency."""
    if not _valid_sha(base_sha) or not _valid_sha(head_sha):
        raise ValueError("Expected full lowercase commit SHA values")
    validate_schema(result, load_schema())
    if result["base_sha"] != base_sha or result["head_sha"] != head_sha:
        raise ValueError("Review result does not match the requested commit range")
    if not result["summary"].strip():
        raise ValueError("Review summary must not be blank")
    blocking = []
    for index, finding in enumerate(result["findings"]):
        path = finding["file"]
        if (
            not path.strip()
            or path.startswith("/")
            or "\\" in path
            or ":" in path
            or any(ord(char) < 32 for char in path)
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            raise ValueError(f"Finding {index}: file must be a safe repository-relative POSIX path")
        if not finding["issue"].strip() or not finding["recommendation"].strip():
            raise ValueError(f"Finding {index}: issue and recommendation must not be blank")
        if finding["severity"] in {"P0", "P1", "P2"}:
            blocking.append(finding)
    if any(not item.strip() for item in result["limitations"]):
        raise ValueError("Review limitations must not contain blank strings")
    if result["status"] == "APPROVED" and (blocking or result["limitations"]):
        raise ValueError("APPROVED cannot contain blocking findings or verification limitations")
    if result["status"] == "CHANGES_REQUESTED" and not blocking:
        raise ValueError("CHANGES_REQUESTED requires at least one P0, P1, or P2 finding")
    return result


def make_blocked(
    base_sha: str,
    head_sha: str,
    summary: str,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Return a schema-valid technical failure even for malformed input SHAs."""
    safe_base = base_sha if _valid_sha(base_sha) else ZERO_SHA
    safe_head = head_sha if _valid_sha(head_sha) else ZERO_SHA
    message = summary if isinstance(summary, str) and summary.strip() else "Review could not be completed."
    if limitations is None:
        safe_limitations = [message]
    else:
        safe_limitations = [str(item) for item in limitations if str(item).strip()]
    result = {
        "status": "BLOCKED",
        "base_sha": safe_base,
        "head_sha": safe_head,
        "summary": message,
        "findings": [],
        "limitations": safe_limitations,
    }
    return validate_result(result, safe_base, safe_head)


def parse_claude_output(raw: str, base_sha: str, head_sha: str) -> dict[str, Any]:
    """Extract only a successful CLI result's schema-constrained output."""
    envelope = strict_json_loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("Claude output must be a JSON result object")
    if envelope.get("is_error") is not False or envelope.get("subtype") != "success":
        raise ValueError("Claude did not return a successful result")
    if envelope.get("permission_denials"):
        raise ValueError("Claude reported denied tool permissions; review may be incomplete")
    if not isinstance(envelope.get("structured_output"), dict):
        raise ValueError("Claude result is missing structured_output")
    return validate_result(envelope["structured_output"], base_sha, head_sha)
