"""A JSON Schema validator covering exactly what platform.schema.json uses.

Not a general implementation, and it does not pretend to be one. The reason
it exists rather than a dependency is that this checker is pinned by a tag
and nothing updates it: a pinned thing with no dependencies cannot break
because of somebody else's release.

The risk that buys is silent under-validation - a schema keyword nobody
implemented, quietly ignored, letting a bad Manifest through. So the
validator refuses to run against a schema using a keyword it does not
implement, and says which one. A future schema edit therefore fails loudly
here rather than passing a Project it should have refused.
"""

import re

SUPPORTED = {
    # assertions
    "type", "required", "properties", "additionalProperties", "enum", "const",
    "pattern", "minLength", "maxLength", "items", "uniqueItems", "minItems",
    "maxItems", "contains", "if", "then", "else",
    # annotations, ignored when validating
    "$schema", "$id", "title", "description", "$comment", "examples", "default",
}

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


class UnsupportedSchema(Exception):
    """The schema uses a keyword this validator does not implement."""


def assert_supported(schema, where="the schema"):
    """Refuse a schema this validator would only partly enforce."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise UnsupportedSchema(where + " is not an object")
    for keyword, value in schema.items():
        if keyword not in SUPPORTED:
            raise UnsupportedSchema(
                "{0} uses the JSON Schema keyword {1!r}, which "
                "contract/schema.py does not implement. Implement it (and "
                "test it) before the schema relies on it - a keyword that is "
                "ignored is a rule that does not exist.".format(where, keyword)
            )
        if keyword == "properties":
            for name, sub in value.items():
                assert_supported(sub, where + " -> properties." + name)
        elif keyword in ("items", "contains", "if", "then", "else"):
            assert_supported(value, where + " -> " + keyword)
        elif keyword == "additionalProperties" and isinstance(value, dict):
            assert_supported(value, where + " -> additionalProperties")


def _type_name(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    for name, cls in TYPES.items():
        if isinstance(value, cls):
            return name
    return type(value).__name__


def _is_type(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    cls = TYPES.get(name)
    if cls is None:
        raise UnsupportedSchema("unknown type " + repr(name))
    if cls is not bool and isinstance(value, bool):
        return False
    return isinstance(value, cls)


def _matches(schema, value):
    return not _errors(schema, value, "")


def _errors(schema, value, path):
    """Validate `value` against `schema`; return human-readable problems."""
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [(path or "the value") + " is not allowed here"]

    where = path or "the Manifest"
    out = []

    expected = schema.get("type")
    if expected is not None:
        wanted = [expected] if isinstance(expected, str) else expected
        if not any(_is_type(value, name) for name in wanted):
            return [
                "{0} must be {1}, not {2}".format(
                    where, " or ".join(wanted), _type_name(value)
                )
            ]

    if "const" in schema and value != schema["const"]:
        out.append("{0} must be {1!r}".format(where, schema["const"]))

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        out.append(
            "{0} must be one of: {1} (found {2!r})".format(where, allowed, value)
        )

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(pattern, value):
            out.append(
                "{0} must match {1} (found {2!r})".format(where, pattern, value)
            )
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(where + " must not be empty")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(
                "{0} must be at most {1} characters".format(
                    where, schema["maxLength"]
                )
            )

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(
                "{0} needs at least {1} entries".format(where, schema["minItems"])
            )
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(
                "{0} allows at most {1} entries".format(where, schema["maxItems"])
            )
        if schema.get("uniqueItems") and len({repr(v) for v in value}) != len(value):
            out.append(where + " must not repeat an entry")
        if "items" in schema:
            for index, item in enumerate(value):
                out.extend(
                    _errors(schema["items"], item, "{0}[{1}]".format(where, index))
                )
        if "contains" in schema and not any(
            _matches(schema["contains"], item) for item in value
        ):
            out.append(where + " is missing a required entry")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                out.append(
                    '{0}: "{1}" is required and is missing'.format(where, name)
                )
        properties = schema.get("properties", {})
        for name, sub in properties.items():
            if name in value:
                out.extend(_errors(sub, value[name], '"' + name + '"'))
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    out.append(
                        '{0}: "{1}" is not a field of the Manifest. '
                        "Known fields: {2}.".format(
                            where, name, ", ".join(sorted(properties))
                        )
                    )

    if "if" in schema:
        branch = "then" if _matches(schema["if"], value) else "else"
        if branch in schema:
            sub = schema[branch]
            problems = _errors(sub, value, path)
            context = sub.get("description") if isinstance(sub, dict) else None
            out.extend(
                (context + ": " + problem) if context else problem
                for problem in problems
            )

    return out


def validate(schema, instance):
    """Return every problem with `instance`, or an empty list."""
    assert_supported(schema)
    return _errors(schema, instance, "")
