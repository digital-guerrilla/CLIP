"""IFC4.3 and COBie mappings into native IFCX and explicit CLIP extensions."""

import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
from urllib.parse import quote

from ..core.ifcx_models import IfcxFile, validate_ifcx_attributes

PREFIX = "urn:clip:construction:"
SOURCE_SCHEMA = PREFIX + "source:v1"
EVENT_SCHEMA = PREFIX + "event:v1"
REFERENCE_SCHEMA = PREFIX + "reference:v1"
EVIDENCE_SCHEMA = PREFIX + "evidence-reference:v1"


def construction_schemas() -> dict:
    return {
        "ifc::name": {"value": {"dataType": "String"}},
        SOURCE_SCHEMA: {"value": {"dataType": "Object", "objectRestrictions": {"values": {
            "format": {"dataType": "String"}, "id": {"dataType": "String"},
            "class": {"dataType": "String"}, "properties": {"dataType": "Object"},
        }}}},
        EVENT_SCHEMA: {"value": {"dataType": "Object", "objectRestrictions": {"values": {
            "kind": {"dataType": "Enum", "enumRestrictions": {"options": ["manufacture", "delivery", "installation", "inspection", "maintenance", "decommission"]}},
            "actorDid": {"dataType": "String"}, "occurredAt": {"dataType": "DateTime"},
            "subject": {"dataType": "Reference"}, "status": {"dataType": "Enum", "enumRestrictions": {"options": ["planned", "complete", "failed", "revoked"]}},
            "evidence": {"dataType": "Reference", "optional": True},
            "previousEvent": {"dataType": "Reference", "optional": True},
        }}}},
        REFERENCE_SCHEMA: {"value": {"dataType": "Reference"}},
        EVIDENCE_SCHEMA: {"value": {"dataType": "Object", "objectRestrictions": {"values": {
            "evidenceId": {"dataType": "String"}, "publisherDid": {"dataType": "String"},
            "uri": {"dataType": "String"}, "integrity": {"dataType": "String"},
        }}}},
    }


def _file(dataset_id: str, author: str, data: list[dict]) -> IfcxFile:
    file = IfcxFile.model_validate({
        "header": {"id": dataset_id, "ifcxVersion": "ifcx_alpha", "dataVersion": "1.0.0", "author": author, "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
        "imports": [], "schemas": construction_schemas(), "data": data,
    })
    validate_ifcx_attributes(file)
    return file


def map_cobie(component_csv: str, type_csv: str, *, dataset_id: str, author: str) -> IfcxFile:
    def rows(content: str):
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if content and (not reader.fieldnames or "Name" not in reader.fieldnames):
            raise ValueError("COBie CSV exports require a Name column")
        output = list(reader)
        names = [row.get("Name", "").strip() for row in output]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("COBie sheet names must be nonempty and unique")
        return output

    types = rows(type_csv)
    components = rows(component_csv)
    type_paths = {row["Name"]: "types/" + quote(row["Name"], safe="") for row in types}
    data = []
    for row in types:
        data.append({"path": type_paths[row["Name"]], "attributes": {
            "ifc::name": row["Name"], SOURCE_SCHEMA: {"format": "COBie", "id": row.get("ExtIdentifier") or row["Name"], "class": "Type", "properties": row},
        }})
    for row in components:
        type_name = row.get("TypeName", "")
        if type_name and type_name not in type_paths:
            raise ValueError(f"COBie component references missing Type {type_name!r}")
        node = {"path": "components/" + quote(row["Name"], safe=""), "attributes": {
            "ifc::name": row["Name"], SOURCE_SCHEMA: {"format": "COBie", "id": row.get("ExtIdentifier") or row["Name"], "class": "Component", "properties": row},
        }, "inherits": {"type": type_paths[type_name]} if type_name else {}}
        data.append(node)
    return _file(dataset_id, author, data)


def map_ifc43(content: str, *, dataset_id: str, author: str) -> IfcxFile:
    import ifcopenshell
    from ifcopenshell.util.element import get_psets, get_type, get_container

    model = ifcopenshell.file.from_string(content)
    if not model.schema.upper().startswith("IFC4X3"):
        raise ValueError("This mapping requires an IFC4.3 source model")
    products = list(model.by_type("IfcObjectDefinition"))
    seen = set()
    paths = {}
    for product in products:
        guid = product.GlobalId
        if not guid or guid in seen:
            raise ValueError("IFC source must contain unique GlobalId values")
        seen.add(guid)
        paths[product.id()] = "ifc/" + quote(guid, safe="")
    data = []
    for product in products:
        properties = get_psets(product)
        node = {"path": paths[product.id()], "attributes": {
            "ifc::name": product.Name or product.GlobalId,
            SOURCE_SCHEMA: {"format": model.schema, "id": product.GlobalId, "class": product.is_a(), "properties": properties},
        }, "inherits": {}, "children": {}}
        product_type = get_type(product)
        if product_type and product_type.id() != product.id() and product_type.id() in paths:
            node["inherits"]["type"] = paths[product_type.id()]
        container = get_container(product)
        if container and container.id() in paths:
            node["attributes"][REFERENCE_SCHEMA] = paths[container.id()]
        data.append(node)
    return _file(dataset_id, author, data)