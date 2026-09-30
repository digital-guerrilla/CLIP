"""Small, deterministic COBie/CSV-to-CLIP mapping adapter."""

import csv
import io
import json
import re
from typing import Any

from ..core.guid import parse_clip
from ..core.models import AssetCreateRequest, AssetSubject, Site

_CLIP_PATTERN = re.compile(r"clip://[^\s,;]+")
_DEFAULT_FIELDS = {
    "name": ("Name", "name", "ComponentName"),
    "manufacturer": ("Manufacturer", "manufacturer"),
    "model_number": ("ModelNumber", "model_number", "TypeName"),
    "serial_number": ("SerialNumber", "serial_number"),
    "building": ("Facility", "Building", "building"),
    "space": ("Space", "space"),
    "ifc_guid": ("IfcGuid", "IFCGUID", "ifc_guid"),
}


def parse_rows(content: str, source_format: str) -> list[dict[str, Any]]:
    if source_format == "json":
        value = json.loads(content)
        if isinstance(value, dict):
            value = value.get("rows", value.get("components", []))
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise ValueError("JSON import must contain an array of row objects")
        return value
    if source_format == "csv":
        return list(csv.DictReader(io.StringIO(content)))
    raise ValueError("source_format must be csv or json")


def map_row(
    row: dict[str, Any],
    *,
    row_number: int,
    mapping: dict[str, str] | None = None,
) -> AssetCreateRequest:
    mapping = mapping or {}

    def value(field: str) -> str | None:
        candidates = (mapping[field],) if field in mapping else _DEFAULT_FIELDS.get(field, ())
        for candidate in candidates:
            result = row.get(candidate)
            if result is not None and str(result).strip():
                return str(result).strip()
        return None

    name = value("name") or value("serial_number") or f"Imported asset row {row_number}"
    linked_clips = extract_clips(row)
    known = {
        "Name", "name", "ComponentName", "Manufacturer", "manufacturer",
        "ModelNumber", "model_number", "TypeName", "SerialNumber", "serial_number",
        "Facility", "Building", "building", "Space", "space", "IfcGuid", "IFCGUID",
        "ifc_guid",
    }
    attributes = {
        "import_row": row_number,
        "source_values": {str(key): str(item) for key, item in row.items() if key not in known and item is not None},
    }
    return AssetCreateRequest(
        record_kind="instance",
        subject=AssetSubject(
            name=name,
            manufacturer=value("manufacturer"),
            model_number=value("model_number"),
            serial_number=value("serial_number"),
            site=Site(
                building=value("building"),
                space=value("space"),
                ifc_guid=value("ifc_guid"),
            ),
            linked_clips=linked_clips,
            attributes=attributes,
        ),
    )


def extract_clips(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for raw in row.values():
        for candidate in _CLIP_PATTERN.findall(str(raw)):
            try:
                normalized = parse_clip(candidate).full_id
            except ValueError:
                continue
            if normalized not in values:
                values.append(normalized)
    return values