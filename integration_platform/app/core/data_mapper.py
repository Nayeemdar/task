"""
Data Mapper — field-level transformation between integration schemas.

Supports:
  - Direct field rename
  - Nested dot-path access
  - Jinja2-style template expressions  "Hello {{first_name}} {{last_name}}"
  - Built-in transform functions: upper, lower, date_format, coalesce, …
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


# ─── Built-in transforms ───────────────────────────────────────────────────────

TRANSFORMS: Dict[str, Callable[[Any], Any]] = {
    "upper":        lambda v: str(v).upper() if v is not None else v,
    "lower":        lambda v: str(v).lower() if v is not None else v,
    "strip":        lambda v: str(v).strip() if v is not None else v,
    "int":          lambda v: int(v) if v is not None else None,
    "float":        lambda v: float(v) if v is not None else None,
    "str":          lambda v: str(v) if v is not None else None,
    "bool":         lambda v: bool(v),
    "to_iso":       lambda v: datetime.fromisoformat(str(v)).isoformat() if v else None,
}


def _get_path(data: Dict, path: str) -> Any:
    """Resolve a dot-notation path in a nested dict. Returns None if not found."""
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            try:
                current = current[int(part)]
            except IndexError:
                return None
        else:
            return None
    return current


def _set_path(data: Dict, path: str, value: Any) -> None:
    """Set a value at a dot-notation path, creating intermediate dicts."""
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


# ─── Main mapper ──────────────────────────────────────────────────────────────

class DataMapper:
    """
    Maps a source dict to a destination dict using a declarative mapping spec.

    Mapping spec format (list of rule dicts):
    [
      # Simple rename
      {"from": "source_field", "to": "dest_field"},

      # Nested path
      {"from": "contact.email", "to": "email_address"},

      # With transform function
      {"from": "name", "to": "full_name_upper", "transform": "upper"},

      # Literal constant
      {"to": "status", "value": "ACTIVE"},

      # Template expression
      {"to": "display_name", "template": "{{first_name}} {{last_name}}"},
    ]
    """

    _EXPR_RE = re.compile(r"\{\{([^}]+)\}\}")

    def __init__(self, mapping: List[Dict]):
        self._mapping = mapping

    def apply(self, source: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        for rule in self._mapping:
            dest = rule.get("to")
            if not dest:
                continue

            if "value" in rule:
                _set_path(result, dest, rule["value"])

            elif "template" in rule:
                rendered = self._render_template(rule["template"], source)
                _set_path(result, dest, rendered)

            elif "from" in rule:
                value = _get_path(source, rule["from"])
                if "transform" in rule:
                    fn = TRANSFORMS.get(rule["transform"])
                    if fn:
                        value = fn(value)
                _set_path(result, dest, value)

        return result

    def _render_template(self, template: str, data: Dict) -> str:
        def replacer(match: re.Match) -> str:
            expr = match.group(1).strip()
            # Support pipe-transforms: "{{name | upper}}"
            if "|" in expr:
                field_path, *transforms = [p.strip() for p in expr.split("|")]
            else:
                field_path, transforms = expr, []

            value = _get_path(data, field_path)
            for t in transforms:
                fn = TRANSFORMS.get(t)
                if fn:
                    value = fn(value)
            return str(value) if value is not None else ""

        return self._EXPR_RE.sub(replacer, template)


# ─── Convenience factory ───────────────────────────────────────────────────────

def build_mapper(mapping: List[Dict]) -> DataMapper:
    return DataMapper(mapping)
