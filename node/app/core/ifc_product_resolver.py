"""Verified shared manufacturer definitions, separate from pinned issue history."""

import copy
import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from ..db.orm_models import SupplyChainRevision
from ..imports.ifcx import SOURCE_SCHEMA
from .ifc_graph import IfcEntity
from .ifcx_models import IfcxSchema

IDENTITY_SCHEMA = "urn:clip:construction:product-identity:v1"
PRODUCT_DATA_SCHEMA = "urn:clip:construction:manufacturer-data:v1"
PRODUCT_REFERENCES_SCHEMA = "urn:clip:construction:product-references:v1"
COMPONENTS_SCHEMA = "urn:clip:construction:component-types:v1"
MAX_PRODUCTS = 128
MAX_SNAPSHOTS = 512


def identity(value):
    key = value["authorityDid"], value.get("recordId", value.get("id"))
    if any(not isinstance(part, str) or not part for part in key):
        raise HTTPException(424, "Manufacturer identity requires a nonempty authority and record ID")
    return key


def product_path(key):
    path = "manufacturer-types/" + hashlib.sha256(
        (key[0] + "\0" + key[1]).encode()
    ).hexdigest()
    return path + ("/revisions/" + str(key[2]) if len(key) == 3 else "")


async def discover_published(session, authority_did):
    from . import supply_chain as service

    service.require_trusted_publisher(authority_did)
    result = (await service.catalogue(session) if authority_did == service.authority()
              else await service.remote(authority_did, "catalogue", {}))
    if result.get("authorityDid") != authority_did:
        raise HTTPException(424, "Catalogue authority mismatch")
    verification = {"documents": {}, "snapshots": set()}
    for item in result["items"]:
        if (item.get("authorityDid") != authority_did or item.get("kind") not in {"product", "offering"}
                or item.get("status") != "published"):
            raise HTTPException(424, "Catalogue contains a non-public/non-owned record")
        await service.verify_snapshot(item, verification=verification)
        await service.cache_snapshot(session, item, public=True)
    return result


async def resolve_products(session, values, *, refresh=False, current=True):
    from . import supply_chain as service

    products = {}
    snapshots = {}
    visited = set()
    verification = {"documents": {}, "snapshots": set()}

    async def dependencies(value):
        if "dependencies" in value:
            return value["dependencies"]
        original = value.get("acceptedFrom", {}).get("snapshot")
        if original:
            embedded = {(*identity(item), item["revision"]): item
                        for item in [original, *original.get("dependencies", [])]}
            result = []
            for source in value.get("sources", []):
                key = (*identity(source), source["revision"])
                result.append(embedded[key] if key in embedded else await service.source_revision(session, source))
            return result
        return [await service.source_revision(session, source) for source in value.get("sources", [])]

    async def visit(value, stack=()):
        key = (*identity(value), value["revision"])
        if key in stack or len(stack) > 64:
            raise HTTPException(424, "Cyclic/oversized product identity dependency")
        if key in visited:
            if "proof" in value and service.digest_json(snapshots[key]) != service.digest_json(value):
                raise HTTPException(409, "Product identity revision equivocation")
            return
        if len(visited) >= MAX_SNAPSHOTS:
            raise HTTPException(422, "Product identity closure exceeds supported size")
        visited.add(key)
        snapshots[key] = value
        if "proof" in value:
            await service.verify_snapshot(value, verification=verification)
        original = value.get("acceptedFrom", {}).get("snapshot")
        if original:
            await visit(original, (*stack, key))
        if value["kind"] == "product" and not original and "proof" in value:
            product_key = identity(value)
            revisions = products.setdefault(product_key, {})
            revisions[value["revision"]] = value
            if len(products) > MAX_PRODUCTS:
                raise HTTPException(422, "Too many shared manufacturer products")
        for dependency in await dependencies(value):
            await visit(dependency, (*stack, key))

    for value in values:
        # Working local records are not signed publications; their source snapshots are.
        for dependency in await dependencies(value):
            await visit(dependency)
        original = value.get("acceptedFrom", {}).get("snapshot")
        if original:
            await visit(original)
        if value["kind"] == "product" and not original:
            row = await session.get(SupplyChainRevision, (*identity(value), value["revision"]))
            if row:
                await visit(row.snapshot_json)

    pinned = {key: set(revisions) for key, revisions in products.items()}
    if refresh:
        if not current:
            raise HTTPException(422, "Pinned graph views cannot refresh manufacturer publications")
        for publisher in sorted({key[0] for key in products}):
            await discover_published(session, publisher)
    if current:
        # Only independently published manufacturer revisions may advance a current definition.
        pending = set(products)
        processed = set()
        refreshed_publishers = {key[0] for key in products} if refresh else set()
        while pending - processed:
            key = sorted(pending - processed)[0]
            processed.add(key)
            if refresh and key[0] not in refreshed_publishers:
                await discover_published(session, key[0])
                refreshed_publishers.add(key[0])
            latest = (await session.execute(select(SupplyChainRevision).where(
                SupplyChainRevision.authority_did == key[0], SupplyChainRevision.record_id == key[1],
                SupplyChainRevision.public.is_(True),
            ).order_by(SupplyChainRevision.revision.desc()).limit(1))).scalar_one_or_none()
            if latest:
                if latest.snapshot_json["ifcClass"] != products[key][min(products[key])]["ifcClass"]:
                    raise HTTPException(409, "Published manufacturer update changes an existing product's IFC class")
                await visit(latest.snapshot_json)
            pending = set(products)

    async def primary(value, stack=()):
        key = (*identity(value), value["revision"])
        if key in stack or len(stack) > 64:
            raise HTTPException(424, "Cyclic product type chain")
        original = value.get("acceptedFrom", {}).get("snapshot")
        if original:
            return await primary(original, (*stack, key))
        if value["kind"] == "product":
            return {identity(value) if current else (*identity(value), value["revision"])}
        result = set()
        for dependency in await dependencies(value):
            result.update(await primary(dependency, (*stack, key)))
        return result

    definitions = []
    for key, revisions in sorted(products.items()):
        for chosen in ([revisions[max(revisions)]] if current else [revisions[revision] for revision in sorted(revisions)]):
            paths = set()
            component_links = []
            for source, dependency in zip(chosen.get("sources", []), await dependencies(chosen)):
                for component in sorted(await primary(dependency)):
                    paths.add(product_path(component))
                    component_links.append({
                        "productPath": product_path(component), "authorityDid": component[0], "recordId": component[1],
                        "quantity": source.get("quantity", 1), "unit": source.get("unit"),
                        "pinnedSource": copy.deepcopy(source),
                    })
            definitions.append({
                "path": product_path(key if current else (*key, chosen["revision"])),
                "authorityDid": key[0], "recordId": key[1],
                "revision": chosen["revision"], "digest": service.digest_json(chosen),
                "pinnedRevisions": sorted(pinned.get(key, set())) if current else [chosen["revision"]],
                "name": chosen["name"], "ifcClass": chosen["ifcClass"],
                "data": copy.deepcopy(chosen["data"]),
                "documents": [{field: copy.deepcopy(value) for field, value in item.items() if field != "content"}
                              for item in chosen.get("documents", [])],
                "componentPaths": sorted(paths), "componentLinks": component_links,
            })
    associations = {}
    component_graph = {definition["path"]: definition["componentPaths"] for definition in definitions}
    checked = set()

    def check_components(path, stack=()):
        if path in stack:
            raise HTTPException(409, "Published product versions form a cyclic canonical component graph")
        if path in checked:
            return
        if path not in component_graph:
            raise HTTPException(424, "Canonical component definition is unavailable")
        for component_path in component_graph[path]:
            check_components(component_path, (*stack, path))
        checked.add(path)

    for path in component_graph:
        check_components(path)
    for value in values:
        associations[value["graphPath"]] = sorted(product_path(key) for key in await primary(value)
                                                  if key[:2] in products)
    return {"definitions": definitions, "associations": associations,
            "mode": "current-published" if current else "pinned", "refreshed": refresh}


async def project_products(session, graph, *, refresh=False, current=True):
    values = []
    for entity in list(graph.entities.values()):
        value = entity.components.get(SOURCE_SCHEMA, {}).get("properties", {}).get("supplyChain")
        if value:
            values.append(value)
    if not values:
        return {"definitions": [], "associations": {}, "mode": "current-published" if current else "pinned",
                "refreshed": False}
    result = await resolve_products(session, values, refresh=refresh, current=current)
    for schema in (IDENTITY_SCHEMA, PRODUCT_DATA_SCHEMA):
        graph.schemas[schema] = IfcxSchema.model_validate({"value": {"dataType": "Object"}})
    for schema in (PRODUCT_REFERENCES_SCHEMA, COMPONENTS_SCHEMA):
        graph.schemas[schema] = IfcxSchema.model_validate({
            "value": {"dataType": "Array", "arrayRestrictions": {"value": {"dataType": "Reference"}}}
        })
    for definition in result["definitions"]:
        existing = graph.entities.get(definition["path"])
        if existing and not existing.components.get(SOURCE_SCHEMA, {}).get("properties", {}).get("canonicalManufacturerType"):
            raise HTTPException(409, "Reserved canonical manufacturer path collides with an authored entity")
        graph.entities[definition["path"]] = IfcEntity(
            path=definition["path"], components={
                "ifc::name": definition["name"],
                IDENTITY_SCHEMA: {field: definition[field] for field in
                                  ("authorityDid", "recordId", "revision", "digest", "pinnedRevisions", "componentLinks")},
                PRODUCT_DATA_SCHEMA: {"data": definition["data"], "documents": definition["documents"]},
                COMPONENTS_SCHEMA: definition["componentPaths"],
                SOURCE_SCHEMA: {"format": "CLIP", "id": definition["recordId"], "class": definition["ifcClass"],
                                "properties": {"canonicalManufacturerType": True}},
            },
        )
    classes = {definition["path"]: definition["ifcClass"] for definition in result["definitions"]}
    for value in values:
        entity = graph.entities[value["graphPath"]]
        paths = result["associations"][value["graphPath"]]
        entity.components[PRODUCT_REFERENCES_SCHEMA] = paths
        if len(paths) == 1 and (value["ifcClass"] == classes[paths[0]]
                               or value["ifcClass"] + "Type" == classes[paths[0]]):
            entity.inherits["manufacturerType"] = paths[0]
            # Upstream type fields live on the shared definition, not editable recipient copies.
            if value["kind"] == "product" and value.get("acceptedFrom"):
                source = copy.deepcopy(entity.components[SOURCE_SCHEMA])
                source["properties"]["supplyChain"]["data"] = {}
                source["properties"]["supplyChain"]["manufacturerDataReference"] = paths[0]
                entity.components[SOURCE_SCHEMA] = source
    return result
