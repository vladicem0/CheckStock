"""Protocol tests use isolated payloads and never call a live review service."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "review"))
from protocol import (
    ZERO_SHA,
    load_schema,
    make_blocked,
    parse_claude_output,
    strict_json_loads,
    validate_result,
    validate_schema,
)

BASE = "a" * 40
HEAD = "b" * 40


def approved():
    return {
        "status": "APPROVED",
        "base_sha": BASE,
        "head_sha": HEAD,
        "summary": "The requested changes satisfy the documented requirements.",
        "findings": [],
        "limitations": [],
    }


def finding(severity="P2"):
    return {
        "severity": severity,
        "file": "app/services/stock.py",
        "line": 42,
        "issue": "A concurrent reservation can oversubscribe the same stock row.",
        "recommendation": "Lock and recheck the current available balance before reserving.",
    }


def changes():
    result = approved()
    result["status"] = "CHANGES_REQUESTED"
    result["findings"] = [finding()]
    return result


def envelope(result=None):
    return {"type": "result", "subtype": "success", "is_error": False,
            "structured_output": approved() if result is None else result}


class ResultTests(unittest.TestCase):
    def test_accepts_approved(self):
        result = approved()
        self.assertIs(validate_result(result, BASE, HEAD), result)

    def test_accepts_changes_requested(self):
        self.assertEqual(validate_result(changes(), BASE, HEAD)["status"], "CHANGES_REQUESTED")

    def test_accepts_blocked(self):
        result = make_blocked(BASE, HEAD, "The requested commit is unavailable.")
        self.assertEqual(validate_result(result, BASE, HEAD)["status"], "BLOCKED")

    def test_p3_does_not_block_approval(self):
        result = approved()
        result["findings"] = [finding("P3")]
        validate_result(result, BASE, HEAD)

    def test_p0_p1_p2_block_approval(self):
        for severity in ("P0", "P1", "P2"):
            with self.subTest(severity=severity):
                result = approved()
                result["findings"] = [finding(severity)]
                with self.assertRaises(ValueError):
                    validate_result(result, BASE, HEAD)

    def test_approval_cannot_hide_limitations(self):
        result = approved()
        result["limitations"] = ["The required acceptance criteria could not be accessed."]
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_changes_requested_needs_blocking_finding(self):
        for findings in ([], [finding("P3")]):
            with self.subTest(findings=findings):
                result = approved()
                result.update(status="CHANGES_REQUESTED", findings=findings)
                with self.assertRaises(ValueError):
                    validate_result(result, BASE, HEAD)

    def test_rejects_wrong_base_or_head(self):
        for field in ("base_sha", "head_sha"):
            result = approved()
            result[field] = "c" * 40
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_result(result, BASE, HEAD)

    def test_rejects_short_uppercase_and_nonhex_sha_arguments(self):
        for sha in ("abc123", "A" * 40, "g" * 40, "a" * 40 + "\n", None):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                validate_result(approved(), sha, HEAD)

    def test_missing_fields_are_not_approval(self):
        for field in approved():
            result = approved()
            del result[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_result(result, BASE, HEAD)

    def test_unknown_result_fields_are_rejected(self):
        result = approved()
        result["approved"] = True
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_unknown_finding_fields_are_rejected(self):
        result = changes()
        result["findings"][0]["verified"] = True
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_rejects_unknown_status_and_severity(self):
        result = approved()
        result["status"] = "PASS"
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)
        result = changes()
        result["findings"][0]["severity"] = "HIGH"
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_rejects_boolean_float_zero_and_negative_line_numbers(self):
        for line in (True, 42.0, 0, -1, "42"):
            result = changes()
            result["findings"][0]["line"] = line
            with self.subTest(line=line), self.assertRaises(ValueError):
                validate_result(result, BASE, HEAD)

    def test_rejects_missing_finding_fields(self):
        for key in finding():
            result = changes()
            del result["findings"][0][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_result(result, BASE, HEAD)

    def test_rejects_unsafe_paths(self):
        paths = ["/etc/passwd", "../stock.py", "app/../stock.py", "C:/stock.py",
                 "app\\stock.py", "//host/share", "app//stock.py", "./stock.py",
                 "app/stock.py\n", "", "   "]
        for path in paths:
            result = changes()
            result["findings"][0]["file"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_result(result, BASE, HEAD)

    def test_accepts_dot_directory_relative_path(self):
        result = changes()
        result["findings"][0]["file"] = ".github/workflows/test.yml"
        validate_result(result, BASE, HEAD)

    def test_rejects_empty_or_blank_text(self):
        for value in ("", "  \n"):
            for field in ("summary", "issue", "recommendation"):
                result = changes()
                if field == "summary":
                    result[field] = value
                else:
                    result["findings"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_result(result, BASE, HEAD)
        result = make_blocked(BASE, HEAD, "Unavailable")
        result["limitations"] = ["  "]
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_metadata_does_not_replace_verdict(self):
        result = approved()
        result["metadata"] = {"status": "CHANGES_REQUESTED", "iteration": 0}
        validate_result(result, BASE, HEAD)
        result["status"] = "CHANGES_REQUESTED"
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_rejects_non_json_metadata(self):
        for metadata in ({"value": float("nan")}, {"value": float("inf")},
                         {1: "not a string key"}, {"value": {"set"}}):
            result = approved()
            result["metadata"] = metadata
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                validate_result(result, BASE, HEAD)

    def test_rejects_excessively_nested_metadata(self):
        nested = {}
        for _ in range(110):
            nested = {"nested": nested}
        result = approved()
        result["metadata"] = nested
        with self.assertRaises(ValueError):
            validate_result(result, BASE, HEAD)

    def test_blocked_builder_sanitizes_invalid_request(self):
        result = make_blocked("bad", "", "")
        self.assertEqual(result["base_sha"], ZERO_SHA)
        self.assertEqual(result["head_sha"], ZERO_SHA)
        validate_result(result, ZERO_SHA, ZERO_SHA)

    def test_blocked_builder_preserves_valid_range_and_explicit_limitations(self):
        result = make_blocked(BASE, HEAD, "Review timed out.", ["No complete review was produced."])
        self.assertEqual(result["limitations"], ["No complete review was produced."])
        self.assertEqual(result["head_sha"], HEAD)


class EnvelopeTests(unittest.TestCase):
    def test_reads_only_structured_output(self):
        value = envelope(changes())
        value["result"] = "APPROVED"
        self.assertEqual(parse_claude_output(json.dumps(value), BASE, HEAD)["status"], "CHANGES_REQUESTED")

    def test_accepts_successful_realistic_envelope(self):
        value = envelope()
        value.update(duration_ms=123, permission_denials=[], session_id="example", usage={})
        self.assertEqual(parse_claude_output(json.dumps(value), BASE, HEAD), approved())

    def test_rejects_free_text_fenced_json_empty_and_arrays(self):
        for raw in ("APPROVED", "```json\n{}\n```", "", "  ", "[]", "null", "{} {}"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_claude_output(raw, BASE, HEAD)

    def test_rejects_error_and_incomplete_envelopes(self):
        for patch in ({"is_error": True}, {"is_error": 0}, {"subtype": "error_max_turns"},
                      {"permission_denials": [{"tool_name": "Read"}]}):
            value = envelope()
            value.update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                parse_claude_output(json.dumps(value), BASE, HEAD)
        for field in ("is_error", "subtype", "structured_output"):
            value = envelope()
            del value[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_claude_output(json.dumps(value), BASE, HEAD)

    def test_rejects_plain_result_and_string_structured_output(self):
        with self.assertRaises(ValueError):
            parse_claude_output(json.dumps(approved()), BASE, HEAD)
        value = envelope()
        value["structured_output"] = json.dumps(approved())
        with self.assertRaises(ValueError):
            parse_claude_output(json.dumps(value), BASE, HEAD)

    def test_rejects_stale_structured_result(self):
        value = envelope()
        value["structured_output"]["head_sha"] = "c" * 40
        with self.assertRaises(ValueError):
            parse_claude_output(json.dumps(value), BASE, HEAD)

    def test_rejects_duplicate_keys(self):
        with self.assertRaises(ValueError):
            strict_json_loads('{"status":"BLOCKED","status":"APPROVED"}')
        with self.assertRaises(ValueError):
            strict_json_loads('{"nested":{"key":1,"key":2}}')

    def test_rejects_nonstandard_constants(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json_loads('{"value":' + value + '}')


class SchemaSubsetTests(unittest.TestCase):
    def test_bundled_schema_validates(self):
        validate_schema(approved(), load_schema())

    def test_unknown_schema_keyword_fails_closed(self):
        schema = load_schema()
        schema["allOf"] = []
        with self.assertRaisesRegex(ValueError, "unsupported schema"):
            validate_schema(approved(), schema)

    def test_nested_unknown_keyword_fails_even_when_property_absent(self):
        schema = load_schema()
        schema["properties"]["metadata"]["unevaluatedProperties"] = False
        with self.assertRaisesRegex(ValueError, "unsupported schema"):
            validate_schema(approved(), schema)

    def test_invalid_schema_types_fail_closed(self):
        for patch in ({"type": "imaginary"}, {"type": ["string", "null"]}, {"required": "status"},
                      {"additionalProperties": {}}, {"pattern": "["},
                      {"minLength": -1}, {"minimum": True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_schema({}, patch)


if __name__ == "__main__":
    unittest.main()
