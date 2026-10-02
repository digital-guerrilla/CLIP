"""Structural models and attribute validation for the pinned IFCX alpha format."""

from __future__ import annotations

from enum import Enum
import hashlib
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
import rfc8785


class IfcxModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class IfcxHeader(IfcxModel):
    id: str
    ifcx_version: str = Field(alias="ifcxVersion")
    data_version: str = Field(alias="dataVersion")
    author: str
    timestamp: str


class IfcxNode(IfcxModel):
    path: str = Field(min_length=1)
    children: dict[str, str | None] = Field(default_factory=dict)
    inherits: dict[str, str | None] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class IfcxImport(IfcxModel):
    uri: str
    integrity: str | None = None


class IfcxDataType(str, Enum):
    REAL = "Real"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    STRING = "String"
    DATE_TIME = "DateTime"
    ENUM = "Enum"
    ARRAY = "Array"
    OBJECT = "Object"
    REFERENCE = "Reference"
    BLOB = "Blob"


class IfcxEnumRestrictions(IfcxModel):
    options: list[str]


class IfcxArrayRestrictions(IfcxModel):
    min: int | float | None = None
    max: int | float | None = None
    value: "IfcxValueDescription"


class IfcxObjectRestrictions(IfcxModel):
    values: dict[str, "IfcxValueDescription"]


class IfcxValueDescription(IfcxModel):
    data_type: IfcxDataType = Field(alias="dataType")
    optional: bool | None = None
    inherits: list[str] | None = None
    quantity_kind: str | None = Field(default=None, alias="quantityKind")
    enum_restrictions: IfcxEnumRestrictions | None = Field(
        default=None,
        alias="enumRestrictions",
    )
    array_restrictions: IfcxArrayRestrictions | None = Field(
        default=None,
        alias="arrayRestrictions",
    )
    object_restrictions: IfcxObjectRestrictions | None = Field(
        default=None,
        alias="objectRestrictions",
    )

    @model_validator(mode="after")
    def validate_type_restrictions(self) -> "IfcxValueDescription":
        if self.data_type == IfcxDataType.ENUM and self.enum_restrictions is None:
            raise ValueError("Enum schemas require enumRestrictions")
        if self.data_type == IfcxDataType.ARRAY and self.array_restrictions is None:
            raise ValueError("Array schemas require arrayRestrictions")
        return self


class IfcxSchema(IfcxModel):
    uri: str | None = None
    value: IfcxValueDescription


class IfcxFile(IfcxModel):
    header: IfcxHeader
    imports: list[IfcxImport]
    schemas: dict[str, IfcxSchema]
    data: list[IfcxNode]


CLIP_COMPONENT_DELETIONS_SCHEMA_ID = "urn:clip:ifcx:component-deletions:v1"


def ensure_clip_component_deletion_schema(file: IfcxFile) -> IfcxFile:
    expected_schema = IfcxSchema(
        uri=CLIP_COMPONENT_DELETIONS_SCHEMA_ID,
        value=IfcxValueDescription(
            data_type=IfcxDataType.ARRAY,
            array_restrictions=IfcxArrayRestrictions(
                value=IfcxValueDescription(data_type=IfcxDataType.STRING),
            ),
        ),
    )
    existing_schema = file.schemas.get(CLIP_COMPONENT_DELETIONS_SCHEMA_ID)
    if existing_schema is not None:
        if existing_schema != expected_schema:
            raise ValueError("CLIP component-deletion schema conflicts with its v1 definition")
        return file
    return file.model_copy(update={
        "schemas": {
            **file.schemas,
            CLIP_COMPONENT_DELETIONS_SCHEMA_ID: expected_schema,
        },
    })


def ifcx_schema_digest(file: IfcxFile) -> str:
    schema_set = {
        "ifcxVersion": file.header.ifcx_version,
        "imports": [item.model_dump(mode="json", by_alias=True) for item in file.imports],
        "schemas": {
            schema_id: schema.model_dump(mode="json", by_alias=True)
            for schema_id, schema in file.schemas.items()
        },
    }
    return hashlib.sha256(rfc8785.dumps(schema_set)).hexdigest()


class IfcxValidationError(ValueError):
    pass


def validate_ifcx_attributes(
    file: IfcxFile,
    *,
    imported_schemas: dict[str, IfcxSchema] | None = None,
) -> None:
    """Validate node attributes against local and already-verified imported schemas."""
    schemas = {**(imported_schemas or {}), **file.schemas}
    for node in file.data:
        for schema_id, value in node.attributes.items():
            schema = schemas.get(schema_id)
            if schema is None:
                raise IfcxValidationError(
                    f'Unknown schema "{schema_id}" at node "{node.path}"'
                )
            _validate_value(schema.value, value, node.path, schemas, frozenset())


def _validate_value(
    description: IfcxValueDescription,
    value: Any,
    path: str,
    schemas: dict[str, IfcxSchema],
    inherited: frozenset[str],
) -> None:
    for schema_id in description.inherits or []:
        if schema_id in inherited:
            raise IfcxValidationError(
                f'Cyclic schema inheritance at "{path}": {schema_id}'
            )
        schema = schemas.get(schema_id)
        if schema is None:
            raise IfcxValidationError(
                f'Unknown inherited schema "{schema_id}" at node "{path}"'
            )
        _validate_value(
            schema.value,
            value,
            path,
            schemas,
            inherited | {schema_id},
        )

    data_type = description.data_type
    valid = False
    if data_type == IfcxDataType.BOOLEAN:
        valid = isinstance(value, bool)
    elif data_type == IfcxDataType.STRING or data_type == IfcxDataType.DATE_TIME:
        valid = isinstance(value, str)
    elif data_type == IfcxDataType.REFERENCE:
        valid = isinstance(value, str)
    elif data_type == IfcxDataType.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif data_type == IfcxDataType.REAL:
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    elif data_type == IfcxDataType.ENUM:
        restrictions = description.enum_restrictions
        if restrictions is None:
            raise IfcxValidationError(f'Missing enum restrictions at "{path}"')
        valid = (
            isinstance(value, str)
            and value in restrictions.options
        )
    elif data_type == IfcxDataType.ARRAY:
        restrictions = description.array_restrictions
        if restrictions is None:
            raise IfcxValidationError(f'Missing array restrictions at "{path}"')
        if not isinstance(value, list):
            raise IfcxValidationError(f'Expected an array at "{path}"')
        if restrictions.min is not None and len(value) < restrictions.min:
            raise IfcxValidationError(f'Array is below its minimum length at "{path}"')
        if restrictions.max is not None and len(value) > restrictions.max:
            raise IfcxValidationError(f'Array exceeds its maximum length at "{path}"')
        for index, item in enumerate(value):
            _validate_value(
                restrictions.value,
                item,
                f"{path}[{index}]",
                schemas,
                inherited,
            )
        return
    elif data_type == IfcxDataType.OBJECT:
        if not isinstance(value, dict):
            raise IfcxValidationError(f'Expected an object at "{path}"')
        restrictions = description.object_restrictions
        if restrictions is not None:
            for key, member_description in restrictions.values.items():
                if key not in value:
                    if member_description.optional:
                        continue
                    raise IfcxValidationError(
                        f'Missing required property "{path}.{key}"'
                    )
                _validate_value(
                    member_description,
                    value[key],
                    f"{path}.{key}",
                    schemas,
                    inherited,
                )
        return
    else:
        raise IfcxValidationError(
            f'Unsupported IFCX datatype "{data_type.value}" at "{path}"'
        )

    if not valid:
        raise IfcxValidationError(
            f'Value at "{path}" does not satisfy datatype "{data_type.value}"'
        )