import json
from typing import Any

import click
import jsonschema


def _json_type(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}") from e


_TYPE_MAP: dict[str, tuple[type | Any, Any]] = {
    "string": (str, None),
    "integer": (int, None),
    "number": (float, None),
    "boolean": (bool, None),
    "object": (_json_type, None),
    "array": (_json_type, None),
}


def build_click_params(input_schema: dict[str, Any]) -> list[click.Parameter]:
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    params: list[click.Parameter] = []
    for name, prop in properties.items():
        schema_type = prop.get("type", "string")
        click_type, default = _TYPE_MAP.get(schema_type, (str, None))
        is_required = name in required
        help_text = prop.get("description", "")
        if schema_type == "boolean":
            params.append(
                click.Option(
                    [f"--{name}/--no-{name}"],
                    default=prop.get("default", False),
                    help=help_text,
                )
            )
        else:
            if "default" in prop:
                default = prop["default"]
                is_required = False
            params.append(
                click.Option(
                    [f"--{name}"],
                    type=click_type,
                    required=is_required,
                    default=default,
                    help=help_text,
                )
            )
    return params


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
    cleaned = {k: v for k, v in args.items() if v is not None}
    try:
        jsonschema.validate(instance=cleaned, schema=schema)
    except jsonschema.ValidationError as e:
        raise click.ClickException(f"Validation error: {e.message}") from e
