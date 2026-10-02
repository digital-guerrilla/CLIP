"""Supported IFC4.3 semantic templates for guided IFCX authoring."""

from uuid import uuid4

from .ifcx_models import IfcxNode
from ..imports.ifcx import SOURCE_SCHEMA

TEMPLATES = {
    "site": ("Site", "IfcSite"),
    "building": ("Building", "IfcBuilding"),
    "storey": ("Storey", "IfcBuildingStorey"),
    "space": ("Space", "IfcSpace"),
    "zone": ("Zone", "IfcZone"),
    "door-type": ("Door product type", "IfcDoorType"),
    "door": ("Door occurrence", "IfcDoor"),
    "pump-type": ("Pump product type", "IfcPumpType"),
    "pump": ("Pump occurrence", "IfcPump"),
    "assembly": ("Physical assembly", "IfcElementAssembly"),
    "group": ("Group", "IfcGroup"),
}
PARENT_CLASSES = {
    "IfcProject": {"IfcSite", "IfcBuilding", "IfcZone", "IfcGroup", "IfcDoorType", "IfcPumpType"},
    "IfcSite": {"IfcBuilding", "IfcZone", "IfcGroup"},
    "IfcBuilding": {"IfcBuildingStorey", "IfcSpace", "IfcDoor", "IfcPump", "IfcElementAssembly"},
    "IfcBuildingStorey": {"IfcSpace", "IfcDoor", "IfcPump", "IfcElementAssembly"},
    "IfcSpace": {"IfcDoor", "IfcPump", "IfcElementAssembly"},
    "IfcElementAssembly": {"IfcDoor", "IfcPump", "IfcElementAssembly"},
}


def author_entity(template: str, name: str, *, type_path: str | None = None) -> IfcxNode:
    import ifcopenshell
    import ifcopenshell.guid

    if template not in TEMPLATES:
        raise ValueError("Choose a supported IFC template")
    class_name = TEMPLATES[template][1]
    schema = ifcopenshell.schema_by_name("IFC4X3_ADD2")
    declaration = schema.declaration_by_name(class_name)
    if declaration.is_abstract():
        raise ValueError("Cannot author an abstract IFC class")
    entity_id = ifcopenshell.guid.compress(uuid4().hex)
    return IfcxNode(path="entities/" + entity_id,
        attributes={"ifc::name": name, SOURCE_SCHEMA: {"format": "CLIP", "id": entity_id,
            "class": class_name, "properties": {"mappingSchema": "IFC4X3_ADD2"}}},
        inherits={"type": type_path} if type_path else {})