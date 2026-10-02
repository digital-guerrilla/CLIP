"""ECS graph view and layer flattening for IFCX documents."""

from typing import Any, Sequence

from pydantic import Field

from .ifcx_models import (
    CLIP_COMPONENT_DELETIONS_SCHEMA_ID,
    IfcxFile,
    IfcxModel,
    IfcxNode,
    IfcxSchema,
    validate_ifcx_attributes,
)


class IfcComponentAddress(IfcxModel):
    """Stable structured address for one component value on one IFCX entity."""

    authority_did: str = Field(alias="authorityDid", pattern=r"^did:")
    dataset_id: str = Field(alias="datasetId", min_length=1)
    entity_path: str = Field(alias="entityPath", min_length=1)
    component_schema_id: str = Field(alias="componentSchemaId", min_length=1)


class IfcEntity(IfcxModel):
    """An IFCX entity: a path-addressed node with schema-typed components."""

    path: str = Field(min_length=1)
    components: dict[str, Any] = Field(default_factory=dict)
    children: dict[str, str] = Field(default_factory=dict)
    inherits: dict[str, str] = Field(default_factory=dict)


class IfcGraph(IfcxModel):
    """Flattened entity/component graph; relationships remain IFCX-native."""

    authority_did: str = Field(alias="authorityDid", pattern=r"^did:")
    dataset_id: str = Field(alias="datasetId", min_length=1)
    entities: dict[str, IfcEntity] = Field(default_factory=dict)
    schemas: dict[str, IfcxSchema] = Field(default_factory=dict)

    def resolve_component(self, address: IfcComponentAddress) -> Any:
        if address.authority_did != self.authority_did or address.dataset_id != self.dataset_id:
            raise ValueError("Component address identifies a different authority or dataset")
        if address.component_schema_id not in self.schemas:
            raise KeyError(f'Unknown IFCX component schema "{address.component_schema_id}"')
        components = self.effective_components(address.entity_path)
        try:
            return components[address.component_schema_id]
        except KeyError as error:
            raise KeyError(
                f'Entity "{address.entity_path}" has no component '
                f'"{address.component_schema_id}"'
            ) from error

    def effective_components(self, entity_path: str) -> dict[str, Any]:
        return self._effective_components(entity_path, frozenset())

    def _effective_components(
        self,
        entity_path: str,
        inheritance_stack: frozenset[str],
    ) -> dict[str, Any]:
        entity = self.entities.get(entity_path)
        if entity is None:
            raise KeyError(f'Unknown IFCX entity "{entity_path}"')
        if entity_path in inheritance_stack:
            raise ValueError(f'Cyclic IFCX inheritance at "{entity_path}"')

        components: dict[str, Any] = {}
        next_stack = inheritance_stack | {entity_path}
        for inherited_path in entity.inherits.values():
            target_path = self._resolve_node_path(inherited_path)
            components.update(self._effective_components(target_path, next_stack))
        components.update({
            schema_id: value
            for schema_id, value in entity.components.items()
            if schema_id != CLIP_COMPONENT_DELETIONS_SCHEMA_ID
        })
        for schema_id in entity.components.get(CLIP_COMPONENT_DELETIONS_SCHEMA_ID, []):
            components.pop(schema_id, None)
        return components

    def _resolve_node_path(self, path: str) -> str:
        if path in self.entities:
            return path

        parts = path.split("/")
        current_path = parts[0]
        if current_path not in self.entities:
            raise KeyError(f'Unknown IFCX inheritance path "{path}"')
        for child_name in parts[1:]:
            current_entity = self.entities[current_path]
            current_path = current_entity.children.get(child_name, "")
            if not current_path or current_path not in self.entities:
                raise KeyError(f'Unknown IFCX inheritance path "{path}"')
        return current_path


def federate_ifc_layers(layers: Sequence[IfcxFile]) -> IfcxFile:
    if not layers:
        raise ValueError("Cannot federate an empty IFCX layer set")
    schemas: dict[str, IfcxSchema] = {}
    data = []
    for layer in layers:
        schemas.update(layer.schemas)
        data.extend(layer.data)
    return IfcxFile(
        header=layers[0].header,
        imports=[],
        schemas=schemas,
        data=data,
    )


def flatten_ifc_layers(
    layers: Sequence[IfcxFile],
    *,
    authority_did: str,
    dataset_id: str,
) -> IfcGraph:
    """Flatten ordered IFCX layers, with later contributions overriding earlier ones."""
    entities: dict[str, dict[str, Any]] = {}
    schemas: dict[str, IfcxSchema] = {}

    for layer in layers:
        schemas.update(layer.schemas)
        for node in layer.data:
            flattened = entities.setdefault(
                node.path,
                {"components": {}, "children": {}, "inherits": {}},
            )
            flattened["components"].update(node.attributes)
            flattened["children"].update(node.children)
            for name, target in node.inherits.items():
                if target is None:
                    flattened["inherits"].pop(name, None)
                else:
                    flattened["inherits"][name] = target

    graph_entities = {
        path: IfcEntity(
            path=path,
            components=contribution["components"],
            children={
                name: target
                for name, target in contribution["children"].items()
                if target is not None
            },
            inherits=contribution["inherits"],
        )
        for path, contribution in entities.items()
    }
    return IfcGraph(
        authorityDid=authority_did,
        datasetId=dataset_id,
        entities=graph_entities,
        schemas=schemas,
    )


def apply_ifc_component_change(
    file: IfcxFile,
    address: IfcComponentAddress,
    *,
    action: str,
    value: Any = None,
    layers: Sequence[IfcxFile] | None = None,
) -> IfcxFile:
    """Return an IFCX file with a new layer contribution for a component set/remove."""
    if action not in {"set", "remove"}:
        raise ValueError("Component action must be set or remove")
    if action == "set" and value is None:
        raise ValueError("A component set operation requires a non-null value")
    if address.component_schema_id == CLIP_COMPONENT_DELETIONS_SCHEMA_ID:
        raise ValueError("The CLIP component-deletion extension is not a public component target")

    base_layers = list(layers) if layers is not None else [file]
    if not base_layers or base_layers[0].header.id != file.header.id:
        raise ValueError("The local IFCX file must be the root layer")
    graph = flatten_ifc_layers(
        base_layers,
        authority_did=address.authority_did,
        dataset_id=address.dataset_id,
    )
    entity = graph.entities.get(address.entity_path)
    if entity is None:
        raise KeyError(f'Unknown IFCX entity "{address.entity_path}"')
    if address.component_schema_id not in graph.schemas:
        raise KeyError(f'Unknown IFCX component schema "{address.component_schema_id}"')

    current_value_exists = address.component_schema_id in graph.effective_components(address.entity_path)
    if action == "remove" and not current_value_exists:
        raise KeyError(
            f'Entity "{address.entity_path}" has no effective component '
            f'"{address.component_schema_id}" to remove'
        )

    tombstones = list(entity.components.get(CLIP_COMPONENT_DELETIONS_SCHEMA_ID, []))
    if action == "remove":
        if address.component_schema_id not in tombstones:
            tombstones.append(address.component_schema_id)
    else:
        tombstones = [
            schema_id for schema_id in tombstones
            if schema_id != address.component_schema_id
        ]

    attributes: dict[str, Any] = {
        CLIP_COMPONENT_DELETIONS_SCHEMA_ID: tombstones,
    }
    if action == "set":
        attributes[address.component_schema_id] = value
    updated = file.model_copy(update={
        "data": [*file.data, IfcxNode(path=address.entity_path, attributes=attributes)],
    })
    federated = federate_ifc_layers([updated, *base_layers[1:]])
    validate_ifcx_attributes(federated)
    return updated


def apply_ifc_graph_operations(
    file: IfcxFile, *, authority_did: str, dataset_id: str,
    operations: Sequence[dict], layers: Sequence[IfcxFile] | None = None,
) -> IfcxFile:
    base_layers = list(layers) if layers is not None else [file]
    if not base_layers or base_layers[0].header.id != file.header.id or file.header.id != dataset_id:
        raise ValueError("Graph operations must target the local root dataset")
    graph = flatten_ifc_layers(base_layers, authority_did=authority_did, dataset_id=dataset_id)
    local_paths = {node.path for node in file.data}
    created_paths = set()
    contributions = []
    for operation in operations:
        contribution = IfcxNode.model_validate(operation["node"])
        if operation["action"] == "create":
            if contribution.path in graph.entities or contribution.path in created_paths:
                raise ValueError("Entity path already exists")
            created_paths.add(contribution.path)
        elif operation["action"] == "contribute":
            if contribution.path not in local_paths:
                raise ValueError("Contributions may target only locally owned entities")
        else:
            raise ValueError("Unknown graph operation")
        contributions.append(contribution)
    updated = file.model_copy(update={"data": [*file.data, *contributions]})
    validate_ifcx_attributes(federate_ifc_layers([updated, *base_layers[1:]]))
    result = flatten_ifc_layers([updated, *base_layers[1:]], authority_did=authority_did, dataset_id=dataset_id)
    visited = set()

    def visit(path: str, stack: frozenset[str]) -> None:
        if path in stack:
            raise ValueError("Cyclic entity containment")
        if path in visited:
            return
        if path not in result.entities:
            raise ValueError("Child target does not exist")
        for target in result.entities[path].children.values():
            visit(target, stack | {path})
        visited.add(path)

    for path in result.entities:
        visit(path, frozenset())
        result.effective_components(path)
    return updated