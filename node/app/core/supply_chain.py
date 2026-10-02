"""Authority-owned supply-chain records backed by accepted IFCX graph changes."""

import base64
import binascii
import copy
import hashlib
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

import nacl.secret
import httpx
from pydantic import ValidationError
from fastapi import HTTPException
from sqlalchemy import select, update

from ..config import settings
from ..dependencies import get_key_manager
from ..db.orm_models import (
    IfcAuthoritySequence, IfcDatasetRecord, ClipProjectPolicy, IfcProposalRecord,
    SupplyChainRecord, SupplyChainRevision, SupplyChainDocument, SupplyChainSubmission,
    SupplyChainOperation, SupplyChainProject,
    SupplyChainDocumentGrant,
    SupplyChainSenderPolicy,
)
from ..db.ifc_store import register_ifc_dataset, append_accepted_transaction, IfcSequenceConflict
from ..imports.ifcx import construction_schemas, SOURCE_SCHEMA
from .ifcx_models import IfcxFile
from .ifc_graph import flatten_ifc_layers
from .ifc_protocol import IfcGraphProposalTransaction, IfcDecisionTransaction, IfcAuthorityReceipt
from .data_integrity import add_data_integrity_proof, verify_data_integrity_proof
from .did import DidVerificationError, resolve_did_web_document, resolve_clip_service_endpoint, resolve_ed25519_verification_key
from .egress import request_json
from ..federation.clip_replication import digest_json

CONTEXT = ["https://w3id.org/security/data-integrity/v2", {"@vocab": "urn:clip:protocol:"}]
KINDS = {"product", "offering", "supply", "installation", "asset"}
RECORD_PROFILE = "urn:clip:supply-chain:record:v1"


def now():
    return datetime.now(timezone.utc)


def timestamp():
    return now().isoformat().replace("+00:00", "Z")


def require_trusted_publisher(did):
    trusted = {value.strip() for value in settings.CLIP_TRUSTED_PUBLISHERS.split(",") if value.strip()}
    if did != authority() and did not in trusted:
        raise HTTPException(403, f'Untrusted supply-chain publisher "{did}"; operator publisher approval is required')


def authority():
    if not settings.DID_WEB_ID.startswith("did:web:"):
        raise HTTPException(503, "Local did:web identity is not configured")
    return settings.DID_WEB_ID


def sign(payload, purpose: Literal["assertionMethod", "capabilityInvocation", "authentication"] = "assertionMethod"):
    payload = copy.deepcopy(payload)
    created = datetime.fromisoformat(payload["created"].replace("Z", "+00:00")) if payload.get("created") else now()
    if created.tzinfo is None:
        raise ValueError("Signed creation time requires a timezone")
    created = created.astimezone(timezone.utc)
    if "created" in payload:
        payload["created"] = created.isoformat().replace("+00:00", "Z")
    return add_data_integrity_proof({"@context": CONTEXT, **payload},
        get_key_manager().private_key_bytes, verification_method=settings.DID_VERIFICATION_METHOD,
        proof_purpose=purpose, created=created)


async def verify(payload, actor, purpose: Literal["assertionMethod", "capabilityInvocation", "authentication"] = "assertionMethod", *, documents=None):
    try:
        document = documents.get(actor) if documents is not None else None
        if document is None:
            document = await resolve_did_web_document(actor)
            if documents is not None:
                documents[actor] = document
        proof = payload["proof"]
        if proof["proofPurpose"] != purpose:
            raise ValueError("Incorrect proof purpose")
        key = resolve_ed25519_verification_key(document, expected_did=actor,
            verification_method=proof["verificationMethod"], proof_purpose=purpose)
        created = datetime.fromisoformat(proof["created"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Proof requires a timezone")
        methods = document.get("verificationMethod", [])
        method = next(item for item in methods if item["id"] == proof["verificationMethod"])
        for field, lower in (("validFrom", True), ("validUntil", False)):
            if field in method:
                bound = datetime.fromisoformat(method[field].replace("Z", "+00:00"))
                if bound.tzinfo is None or (created < bound if lower else created >= bound):
                    raise ValueError("Proof key is outside its validity interval")
        if not verify_data_integrity_proof(payload, key):
            raise ValueError("Signature verification failed")
        return document
    except (DidVerificationError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise HTTPException(424, f"Unverifiable authority proof: {error}") from error


def local_verify(payload, purpose):
    import nacl.signing
    key = nacl.signing.SigningKey(get_key_manager().private_key_bytes).verify_key
    if payload["proof"]["proofPurpose"] != purpose or not verify_data_integrity_proof(payload, key):
        raise HTTPException(500, "Local signature verification failed")


def service_message(recipient, action, payload, *, request_id=None):
    message = sign({"messageId": str(uuid4()), "actorDid": authority(), "audienceDid": recipient,
        "action": action, "payload": payload,
        **({"requestId": request_id} if request_id else {}),
        "created": now().isoformat().replace("+00:00", "Z")}, "authentication")
    local_verify(message, "authentication")
    return message


async def remote(recipient, action, payload):
    try:
        document = await resolve_did_web_document(recipient)
        endpoint = await resolve_clip_service_endpoint(document, expected_did=recipient,
            service_type="ClipSupplyChainService", fragment="clip-supply-chain")
        message = service_message(recipient, action, payload)
        result = await request_json("POST", endpoint, body=message)
        await verify(result, recipient, "authentication")
        if result.get("audienceDid") != authority() or result.get("action") != action + "Response" or result.get("requestId") != message["messageId"]:
            raise ValueError("Remote response audience/action mismatch")
        return result["payload"]
    except HTTPException:
        raise
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {403, 409}:
            try:
                detail = error.response.json().get("detail", str(error))
            except ValueError:
                detail = str(error)
            raise HTTPException(error.response.status_code, "Remote authority: " + str(detail)) from error
        raise HTTPException(424, f"Remote supply-chain service unavailable ({type(error).__name__}): {error}") from error
    except (DidVerificationError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(424, f"Remote supply-chain service unavailable ({type(error).__name__}): {error}") from error


async def record(session, record_id):
    row = await session.get(SupplyChainRecord, record_id)
    if row is None or row.authority_did != authority():
        raise HTTPException(404, "Unknown owned record")
    return row


async def precondition(session, row, expected):
    result = await session.execute(update(SupplyChainRecord).where(
        SupplyChainRecord.record_id == row.record_id, SupplyChainRecord.revision == expected,
    ).values(revision=expected + 1))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "Record changed; refresh before saving")
    row.revision = expected + 1


async def workspace(session, project_id=None):
    did = authority()
    if project_id:
        dataset = await session.get(IfcDatasetRecord, (did, project_id))
        if dataset:
            policy = await session.get(ClipProjectPolicy, (did, project_id))
            if policy is None or policy.visibility != "private":
                raise HTTPException(409, "Private supply-chain records require a private project")
            return dataset
        connected = (await session.execute(select(SupplyChainProject).where(
            SupplyChainProject.project_id == project_id))).scalars().all()
        if not any(row.project_json.get("status") == "accepted" and row.project_json.get("visibility") == "private" for row in connected):
            raise HTTPException(404, "Unknown private local or accepted connected project; connect an explicit destination first")
        # Remote destinations label local owned records; they never transfer graph authority.
    dataset_id = "urn:clip:supply-chain:" + hashlib.sha256(did.encode()).hexdigest()
    dataset = await session.get(IfcDatasetRecord, (did, dataset_id))
    if dataset:
        return dataset
    file = IfcxFile.model_validate({"header": {"id": dataset_id, "ifcxVersion": "ifcx_alpha",
        "dataVersion": "1.0.0", "author": did, "timestamp": timestamp()},
        "imports": [], "schemas": construction_schemas(), "data": []})
    await register_ifc_dataset(session, authority_did=did, dataset_id=dataset_id, file=file,
        project=ClipProjectPolicy(authority_did=did, dataset_id=dataset_id, name="Private supply-chain workspace",
            visibility="private", members={}, revision=1, updated_at=now()))
    return await session.get(IfcDatasetRecord, (did, dataset_id))


async def graph_commit(session, dataset, values, *, create=False):
    """Commit records and graph proposals/decisions/receipts in one database transaction."""
    did = authority()
    state = await session.get(IfcAuthoritySequence, did)
    sequence = state.sequence if state else 0
    operations = []
    existing_file = IfcxFile.model_validate(dataset.file_json)
    existing_graph = flatten_ifc_layers([existing_file], authority_did=did, dataset_id=dataset.dataset_id)
    for value in values:
        node = {
            "path": value["graphPath"], "attributes": {"ifc::name": value["name"],
                SOURCE_SCHEMA: {"format": "CLIP", "id": value["id"], "class": value["ifcClass"],
                    "properties": {"supplyChain": value}}}}
        if value["graphPath"] in existing_graph.entities:
            original_source = existing_graph.entities[value["graphPath"]].components.get(SOURCE_SCHEMA, {})
            original_properties = copy.deepcopy(original_source.get("properties", {}))
            original_properties.update(copy.deepcopy(value["data"].get("nativeProperties", {})))
            original_properties["supplyChain"] = value
            node["attributes"][SOURCE_SCHEMA] = {**copy.deepcopy(original_source),
                "class": value["ifcClass"], "properties": original_properties}
        if value["kind"] == "installation" and len(value["sources"]) == 1:
            source = await source_revision(session, value["sources"][0])
            if source["authorityDid"] == did and source["datasetId"] == dataset.dataset_id and source["ifcClass"].endswith("Type"):
                node["inherits"] = {"type": source["graphPath"]}
        operations.append({"action": "create" if create else "contribute", "node": node})
    unsigned = {"transactionId": str(uuid4()), "actorDid": did,
        "target": {"authorityDid": did, "datasetId": dataset.dataset_id},
        "operations": operations, "expectedSequence": sequence, "schemaDigest": dataset.schema_digest,
        "created": timestamp()}
    proposal = IfcGraphProposalTransaction.model_validate(sign(unsigned))
    proposal_json = sign(proposal.model_dump(mode="json", by_alias=True, exclude={"proof"}))
    local_verify(proposal_json, "assertionMethod")
    proposal_digest = digest_json(proposal_json)
    session.add(IfcProposalRecord(proposal_id=unsigned["transactionId"], authority_did=did,
        dataset_id=dataset.dataset_id, proposer_did=did, proposal_digest=proposal_digest,
        proposal_json=proposal_json, status="pending", created_at=now(), updated_at=now()))
    await session.flush()
    decision = IfcDecisionTransaction.model_validate(sign({"transactionId": str(uuid4()),
        "actorDid": did, "decision": "accept", "proposalId": unsigned["transactionId"],
        "proposalDigest": proposal_digest, "expectedSequence": sequence,
        "created": timestamp()}, "capabilityInvocation"))
    decision_json = sign(decision.model_dump(mode="json", by_alias=True, exclude={"proof"}), "capabilityInvocation")
    local_verify(decision_json, "capabilityInvocation")

    def receipt(next_sequence, digest):
        typed = IfcAuthorityReceipt.model_validate(sign({"receiptId": str(uuid4()), "authorityDid": did,
            "transactionId": decision_json["transactionId"], "accepted": True, "sequence": next_sequence,
            "transactionDigest": digest, "created": timestamp()}))
        canonical = sign(typed.model_dump(mode="json", by_alias=True, exclude={"proof"}))
        local_verify(canonical, "assertionMethod")
        return IfcAuthorityReceipt.model_validate(canonical)

    try:
        return await append_accepted_transaction(session, authority_did=did, dataset_id=dataset.dataset_id,
            expected_sequence=sequence, transaction=decision_json, proposal_id=unsigned["transactionId"],
            create_receipt=receipt)
    except IfcSequenceConflict as error:
        raise HTTPException(409, str(error)) from error


async def source_revision(session, source):
    require_trusted_publisher(source["authorityDid"])
    key = (source["authorityDid"], source["recordId"], source["revision"])
    row = await session.get(SupplyChainRevision, key)
    if row is None:
        raise HTTPException(424, "Pinned source revision is unavailable; discover its authority or accept its submission first")
    return copy.deepcopy(row.snapshot_json)


def validate_ifc_class(value):
    import ifcopenshell
    try:
        declaration = ifcopenshell.schema_by_name("IFC4X3_ADD2").declaration_by_name(value["ifcClass"]).as_entity()
        if declaration is None or declaration.is_abstract():
            raise ValueError("IFC class is abstract")
        if value["kind"] in {"product", "offering", "supply"} and not value["ifcClass"].endswith("Type"):
            raise ValueError("Product/offering/supply must reference an IFC product type class")
        if value["kind"] in {"installation", "asset"} and value["ifcClass"].endswith("Type"):
            raise ValueError("Installation/asset must use an occurrence class")
    except (RuntimeError, ValueError, AttributeError) as error:
        raise HTTPException(422, f"Unsupported IFC class: {error}") from error


async def validate_sources(session, value):
    sources = value["sources"]
    if len({key_for_source(item) for item in sources}) != len(sources):
        raise HTTPException(422, "Source record associations must be distinct")
    if value["kind"] in {"offering", "supply", "installation"} and not sources:
        raise HTTPException(422, "This record requires a pinned source")
    allowed = {"product": {"product", "offering"}, "offering": {"product", "offering"},
        "supply": {"offering"}, "installation": {"supply", "installation", "asset"},
        "asset": {"installation", "asset", "supply"}}
    ancestry = []
    validate_ifc_class(value)
    if value["kind"] in {"offering", "supply"} and len(sources) != 1:
        raise HTTPException(422, "Offering/supply must pin exactly one upstream product or offering")
    if value.get("acceptedFrom") and value["kind"] == "supply":
        original = value["acceptedFrom"]["snapshot"]
        if any(value["data"].get(field) != original["data"].get(field) for field in ("quantity", "unit", "serials")) or value["ifcClass"] != original["ifcClass"]:
            raise HTTPException(422, "Accepted upstream supply allocations are immutable; submit a correction")
    for source in sources:
        upstream = await source_revision(session, source)
        digest = digest_json(upstream)
        if source.get("digest") and source["digest"] != digest:
            raise HTTPException(424, "Pinned source digest mismatch")
        if source.get("datasetId") and source["datasetId"] != upstream["datasetId"] or source.get("entityPath") and source["entityPath"] != upstream["graphPath"]:
            raise HTTPException(424, "Source dataset/entity address mismatch")
        source.update(digest=digest, datasetId=upstream["datasetId"],
            entityPath=upstream["graphPath"], componentSchema=SOURCE_SCHEMA)
        if upstream["kind"] not in allowed[value["kind"]]:
            raise HTTPException(422, "Incompatible source record kind")
        if value["kind"] in {"offering", "supply"} and value["ifcClass"] != upstream["ifcClass"]:
            raise HTTPException(422, "Offering/supply IFC type must match its pinned source")
        chain = upstream.get("lineage", []) + [{"authorityDid": upstream["authorityDid"],
            "recordId": upstream["id"], "revision": upstream["revision"], "kind": upstream["kind"],
            "datasetId": upstream["datasetId"], "entityPath": upstream["graphPath"], "digest": digest}]
        if any(item["authorityDid"] == authority() and item["recordId"] == value["id"] for item in chain):
            raise HTTPException(422, "Source dependency cycle")
        if len(chain) > 64:
            raise HTTPException(422, "Source chain exceeds supported depth")
        if value["kind"] == "product":
            quantity = source.get("quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0 or not source.get("unit"):
                raise HTTPException(422, "Components require positive quantity and unit")
            expected = source.get("ifcClass")
            if expected and upstream["ifcClass"] != expected:
                raise HTTPException(422, "Component IFC class is incompatible")
        if value["kind"] == "installation" and upstream["kind"] == "supply":
            if not upstream.get("acceptedFrom"):
                raise HTTPException(422, "Installation requires accepted supply")
            if upstream["authorityDid"] != authority():
                raise HTTPException(422, "Allocate a recipient-owned accepted supply record, not a remote source snapshot")
            if upstream.get("projectId") != value.get("projectId"):
                raise HTTPException(422, "Installation allocation must belong to the accepted supply's local project")
            # Serialize allocations against the physical supply across different installation records.
            locked_supply = await session.execute(update(SupplyChainRecord).where(
                SupplyChainRecord.record_id == upstream["id"],
                SupplyChainRecord.authority_did == authority(),
            ).values(revision=SupplyChainRecord.revision))
            if locked_supply.rowcount != 1:
                raise HTTPException(424, "Accepted local supply allocation authority is unavailable")
            original = upstream["acceptedFrom"]["snapshot"]
            accepted_supplies = (await session.execute(select(SupplyChainRecord).where(SupplyChainRecord.kind == "supply"))).scalars()
            for accepted_supply in accepted_supplies:
                corrected = accepted_supply.record_json.get("acceptedFrom", {}).get("snapshot")
                if corrected and (corrected["authorityDid"], corrected["id"]) == (original["authorityDid"], original["id"]) and corrected["revision"] > original["revision"]:
                    raise HTTPException(409, "Accepted supply was superseded by a reviewed correction; use its current revision")
            quantity = source.get("quantity")
            available = upstream["data"].get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0 or not isinstance(available, (int, float)):
                raise HTTPException(422, "Installation allocation requires a positive quantity")
            if source.get("unit") != upstream["data"].get("unit"):
                raise HTTPException(422, "Installation allocation units must match supply")
            all_records = (await session.execute(select(SupplyChainRecord).where(SupplyChainRecord.kind == "installation"))).scalars()
            allocated = Decimal(0)
            serials = source.get("serials", [])
            if len(serials) != len(set(serials)) or len(serials) > quantity or not set(serials) <= set(upstream["data"].get("serials", [])):
                raise HTTPException(422, "Invalid serial allocation")
            for other in all_records:
                if other.record_id == value["id"]:
                    continue
                for allocation in other.record_json["sources"]:
                    same_supply = key_for_source(allocation) == key_for_source(source)
                    if not same_supply:
                        other_supply = await session.get(SupplyChainRevision, (
                            allocation["authorityDid"], allocation["recordId"], allocation["revision"]))
                        if other_supply and other_supply.snapshot_json.get("acceptedFrom"):
                            original_other = other_supply.snapshot_json["acceptedFrom"]["snapshot"]
                            original_current = upstream["acceptedFrom"]["snapshot"]
                            same_supply = (original_other["authorityDid"], original_other["id"]) == (
                                original_current["authorityDid"], original_current["id"])
                    if same_supply:
                        allocated += Decimal(str(allocation.get("quantity", 0)))
                        if set(serials) & set(allocation.get("serials", [])):
                            raise HTTPException(409, "Serial is already allocated")
            if allocated + Decimal(str(quantity)) > Decimal(str(available)):
                raise HTTPException(409, "Allocation exceeds accepted supply quantity")
            source_type = upstream.get("ifcClass", "")
            if source_type.endswith("Type") and value["ifcClass"] != source_type[:-4]:
                raise HTTPException(422, "Occurrence IFC class is incompatible with supply type")
        ancestry.extend(chain)
    value["lineage"] = ancestry
    if value["kind"] == "supply":
        quantity = value["data"].get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0 or not value["data"].get("unit"):
            raise HTTPException(422, "Supply requires positive quantity and unit")
        serials = value["data"].get("serials", [])
        if len(serials) != len(set(serials)) or len(serials) > quantity:
            raise HTTPException(422, "Supply serials must be unique and not exceed quantity")


def key_for_source(source):
    return source["authorityDid"], source["recordId"]


async def save_record(session, body, record_id=None):
    operation = ("record:save:" + (record_id or "new") + ":" + body["idempotencyKey"]) if body.get("idempotencyKey") else None
    if operation:
        previous = await replay(session, operation, body)
        if previous:
            return previous
    if body["kind"] not in KINDS:
        raise HTTPException(422, "Unsupported record kind")
    existing = await record(session, record_id) if record_id else None
    if existing and body["kind"] != existing.kind:
        raise HTTPException(422, "Record kind cannot change")
    project_id = body.get("projectId")
    if body["kind"] == "installation":
        policy = await session.get(ClipProjectPolicy, (authority(), project_id)) if project_id else None
        if policy is None or policy.visibility != "private":
            raise HTTPException(422, "Installation requires a known local private project")
    if existing and project_id != existing.project_id:
        raise HTTPException(422, "Record project cannot change")
    dataset = await session.get(IfcDatasetRecord, (authority(), existing.record_json["datasetId"])) if existing else await workspace(session, project_id)
    if dataset is None:
        raise HTTPException(424, "Owned native record dataset is unavailable")
    policy = await session.get(ClipProjectPolicy, (authority(), dataset.dataset_id))
    if policy is None or policy.visibility != "private":
        raise HTTPException(409, "Owned native record dataset is no longer private")
    value = {"profile": RECORD_PROFILE, "id": record_id or str(uuid4()), "authorityDid": authority(), "kind": body["kind"],
        "name": body["name"], "projectId": project_id, "ifcClass": body.get("ifcClass") or "IfcBuildingElementProxyType",
        "data": body.get("data", {}), "sources": body.get("sources", []),
        "documents": copy.deepcopy(existing.record_json["documents"]) if existing else [],
        "revision": existing.revision + 1 if existing else 1, "status": "draft",
        "datasetId": dataset.dataset_id, "graphPath": existing.record_json["graphPath"] if existing else "supply-chain/" + str(uuid4())}
    if existing:
        for field in ("acceptedFrom", "originalKind"):
            if field in existing.record_json:
                value[field] = existing.record_json[field]
        if existing.record_json.get("acceptedFrom") and body.get("sources", []) != existing.record_json["sources"]:
            # Accepted upstream lineage is not an editable recipient-authored source assertion.
            original_sources = existing.record_json["sources"]
            if [(item["authorityDid"], item["recordId"], item["revision"]) for item in body.get("sources", [])] != [
                (item["authorityDid"], item["recordId"], item["revision"]) for item in original_sources]:
                raise HTTPException(422, "Accepted source lineage cannot change; create an onward record or correction")
    await validate_sources(session, value)
    if existing:
        await precondition(session, existing, body["expectedRevision"])
        existing.record_json = value
    else:
        session.add(SupplyChainRecord(record_id=value["id"], authority_did=authority(), kind=value["kind"],
            project_id=project_id, revision=1, record_json=value))
    if operation:
        remember(session, operation, body, value)
    await graph_commit(session, dataset, [value], create=not existing)
    return value


async def adopt_record(session, body):
    operation = "record:adopt:" + body["idempotencyKey"]
    old = await replay(session, operation, body)
    if old:
        return old
    did = authority()
    dataset = await session.get(IfcDatasetRecord, (did, body["datasetId"]))
    if dataset is None:
        raise HTTPException(404, "Unknown locally owned dataset")
    policy = await session.get(ClipProjectPolicy, (did, dataset.dataset_id))
    if policy is None or policy.visibility != "private":
        raise HTTPException(409, "Adoption requires a private native dataset; public libraries cannot acquire private workflow metadata")
    state = await session.get(IfcAuthoritySequence, did)
    if body["expectedSequence"] != (state.sequence if state else 0):
        raise HTTPException(409, "Authority sequence changed; refresh before adopting")
    file = IfcxFile.model_validate(dataset.file_json)
    if file.imports:
        raise HTTPException(409, "Adoption requires an import-free native dataset; overlapping imported contributions have ambiguous ownership")
    if body["entityPath"] not in {node.path for node in file.data}:
        raise HTTPException(403, "Only locally authored native paths can be adopted, never imported effective entities")
    graph = flatten_ifc_layers([file], authority_did=did, dataset_id=dataset.dataset_id)
    entity = graph.entities[body["entityPath"]]
    source = entity.components.get(SOURCE_SCHEMA, {})
    if source.get("format") not in {"CLIP", "IFC", "COBie"} or not source.get("class", "").endswith("Type"):
        raise HTTPException(422, "Adoption requires a concrete native IFC product type")
    owned = (await session.execute(select(SupplyChainRecord).where(SupplyChainRecord.authority_did == did))).scalars()
    if any(item.record_json["datasetId"] == dataset.dataset_id and item.record_json["graphPath"] == body["entityPath"] for item in owned):
        raise HTTPException(409, "Native product type already has a workflow record")
    value = {"profile": RECORD_PROFILE, "id": str(uuid4()), "authorityDid": did, "kind": "product",
        "name": entity.components.get("ifc::name", body["entityPath"]), "projectId": None,
        "ifcClass": source["class"], "data": {"nativeProperties": copy.deepcopy(source.get("properties", {}))},
        "sources": [], "documents": [], "revision": 1, "status": "draft",
        "datasetId": dataset.dataset_id, "graphPath": body["entityPath"], "lineage": []}
    validate_ifc_class(value)
    session.add(SupplyChainRecord(record_id=value["id"], authority_did=did, kind="product",
        project_id=None, revision=1, record_json=value))
    remember(session, operation, body, value)
    await graph_commit(session, dataset, [value], create=False)
    return value


async def adoption_candidates(session):
    did = authority()
    state = await session.get(IfcAuthoritySequence, did)
    datasets = (await session.execute(select(IfcDatasetRecord, ClipProjectPolicy).join(
        ClipProjectPolicy, (ClipProjectPolicy.authority_did == IfcDatasetRecord.authority_did) &
        (ClipProjectPolicy.dataset_id == IfcDatasetRecord.dataset_id)
    ).where(IfcDatasetRecord.authority_did == did, ClipProjectPolicy.visibility == "private"))).all()
    records = (await session.execute(select(SupplyChainRecord).where(SupplyChainRecord.authority_did == did))).scalars()
    adopted = {(row.record_json["datasetId"], row.record_json["graphPath"]) for row in records}
    candidates = []
    for dataset, policy in datasets:
        file = IfcxFile.model_validate(dataset.file_json)
        if file.imports:
            continue
        graph = flatten_ifc_layers([file], authority_did=did, dataset_id=dataset.dataset_id)
        for entity_path, entity in graph.entities.items():
            source = entity.components.get(SOURCE_SCHEMA, {})
            if (dataset.dataset_id, entity_path) in adopted or source.get("format") not in {"CLIP", "IFC", "COBie"} or not source.get("class", "").endswith("Type"):
                continue
            try:
                validate_ifc_class({"kind": "product", "ifcClass": source["class"]})
            except HTTPException as error:
                if error.status_code == 422:
                    continue
                raise
            candidates.append({"datasetId": dataset.dataset_id, "entityPath": entity_path,
                "name": entity.components.get("ifc::name", entity_path), "ifcClass": source["class"],
                "properties": source.get("properties", {}), "projectName": policy.name, "visibility": "private"})
    sequence = state.sequence if state else 0
    return {"authorityDid": did, "sequence": sequence, "expectedSequence": sequence, "items": candidates}


def box():
    return nacl.secret.SecretBox(hashlib.sha256(get_key_manager().private_key_bytes + b"clip-supply-chain-documents-v1").digest())


async def upload_document(session, row, body):
    operation = ("document:upload:" + row.record_id + ":" + body["idempotencyKey"]) if body.get("idempotencyKey") else None
    if operation:
        previous = await replay(session, operation, body)
        if previous:
            return previous
    try:
        content = base64.b64decode(body["content"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(422, "Document content must be base64") from error
    if not content or len(content) > min(settings.MAX_DOCUMENT_BYTES, settings.CLIP_MAX_SERVICE_BYTES // 2):
        raise HTTPException(413, "Document is empty or too large")
    if body["visibility"] == "public" and row.kind not in {"product", "offering"}:
        raise HTTPException(422, "Project evidence cannot be published as catalogue documentation")
    replaced_id = body.get("replacesDocumentId")
    if replaced_id:
        replaced = await session.get(SupplyChainDocument, replaced_id)
        if not replaced or replaced.record_id != row.record_id or not any(
            item["id"] == replaced_id for item in row.record_json["documents"]):
            raise HTTPException(403, "Replacement requires a currently attached document owned by this record")
    await precondition(session, row, body["expectedRevision"])
    metadata = {"id": str(uuid4()), "name": body["name"], "mediaType": body["mediaType"],
        "visibility": body["visibility"], "digest": hashlib.sha256(content).hexdigest(),
        "size": len(content), "authorityDid": authority(), "recordId": row.record_id}
    if replaced_id:
        metadata["supersedesDocumentId"] = replaced_id
    session.add(SupplyChainDocument(document_id=metadata["id"], record_id=row.record_id,
        metadata_json=metadata, ciphertext=bytes(box().encrypt(content))))
    value = copy.deepcopy(row.record_json)
    if replaced_id:
        value["documents"] = [item for item in value["documents"] if item["id"] != replaced_id]
    value["documents"].append(metadata)
    value["revision"] = row.revision
    value["status"] = "draft"
    row.record_json = value
    result = {**metadata, "recordRevision": row.revision}
    if operation:
        remember(session, operation, body, result)
    dataset = await session.get(IfcDatasetRecord, (authority(), value["datasetId"]))
    await graph_commit(session, dataset, [value])
    return result


async def detach_document(session, row, document_id, body):
    operation = ("document:detach:" + row.record_id + ":" + document_id + ":" + body["idempotencyKey"]) if body.get("idempotencyKey") else None
    if operation:
        old = await replay(session, operation, body)
        if old:
            return old
    document = await session.get(SupplyChainDocument, document_id)
    if not document or document.record_id != row.record_id or not any(
        item["id"] == document_id for item in row.record_json["documents"]):
        raise HTTPException(403, "Only a currently attached owned document can be retired")
    await precondition(session, row, body["expectedRevision"])
    value = copy.deepcopy(row.record_json)
    value["documents"] = [item for item in value["documents"] if item["id"] != document_id]
    value["revision"] = row.revision
    value["status"] = "draft"
    row.record_json = value
    result = {"recordId": row.record_id, "documentId": document_id, "recordRevision": row.revision,
        "status": "detached", "historicalBytesRetained": True}
    if operation:
        remember(session, operation, body, result)
    dataset = await session.get(IfcDatasetRecord, (authority(), value["datasetId"]))
    if dataset is None:
        raise HTTPException(424, "Owned record dataset is unavailable")
    await graph_commit(session, dataset, [value])
    return result


async def freeze(session, row, *, public=False):
    key = (authority(), row.record_id, row.revision)
    old = await session.get(SupplyChainRevision, key)
    if old:
        if public and not old.public:
            raise HTTPException(409, "Private issued revision cannot be republished; edit to create a public revision")
        return old.snapshot_json
    snapshot = copy.deepcopy(row.record_json)
    snapshot["status"] = "published" if public else "issued"
    # Restricted attachment metadata travels only in explicitly selected issue evidence.
    snapshot["documents"] = [item for item in snapshot["documents"] if item["visibility"] == "public"]
    if public:
        if row.kind not in {"product", "offering"}:
            raise HTTPException(422, "Only product types and offerings have public catalogue revisions")
        if row.project_id:
            raise HTTPException(422, "Project-scoped records cannot be published")
        forbidden = {"price", "prices", "client", "clientDetails", "deliveryAddress", "serials", "batch", "allocations"}
        if forbidden & set(snapshot["data"]):
            raise HTTPException(422, "Catalogue data includes private delivery/commercial fields; remove them before publishing")
        snapshot["documents"] = [item for item in snapshot["documents"] if item["visibility"] == "public"]
        # Public bytes are deliberately captured in this signed immutable revision.
        for item in snapshot["documents"]:
            document = await session.get(SupplyChainDocument, item["id"])
            item["content"] = base64.b64encode(box().decrypt(document.ciphertext)).decode()
        for source in snapshot["sources"]:
            upstream = await session.get(SupplyChainRevision, (source["authorityDid"], source["recordId"], source["revision"]))
            if not upstream or not upstream.public:
                raise HTTPException(424, "Public catalogue cannot disclose a private dependency")
    snapshot["dependencies"] = [await source_revision(session, source) for source in snapshot["sources"]]
    snapshot = sign(snapshot)
    local_verify(snapshot, "assertionMethod")
    session.add(SupplyChainRevision(authority_did=authority(), record_id=row.record_id,
        revision=row.revision, public=public, snapshot_json=snapshot))
    if public:
        value = copy.deepcopy(row.record_json)
        value["status"] = "published"
        row.record_json = value
    await session.flush()
    return snapshot


async def verify_snapshot(snapshot, visited=None, *, verification=None):
    visited = visited or set()
    verification = verification if verification is not None else {"documents": {}, "snapshots": set()}
    try:
        key = (snapshot["authorityDid"], snapshot["id"], snapshot["revision"])
        if key in visited or len(visited) > 64:
            raise ValueError("Cyclic/oversized snapshot dependency")
        snapshot_digest = digest_json(snapshot)
        if snapshot_digest in verification["snapshots"]:
            return
        await verify(snapshot, snapshot["authorityDid"], documents=verification["documents"])
        if snapshot["kind"] not in KINDS or snapshot.get("profile") != RECORD_PROFILE:
            raise ValueError("Invalid record kind")
        require_trusted_publisher(snapshot["authorityDid"])
        if not isinstance(snapshot["revision"], int) or isinstance(snapshot["revision"], bool) or snapshot["revision"] < 1:
            raise ValueError("Invalid record revision")
        if not isinstance(snapshot["name"], str) or not snapshot["name"].strip() or not isinstance(snapshot["data"], dict):
            raise ValueError("Invalid record name/data")
        validate_ifc_class(snapshot)
        dependencies = snapshot.get("dependencies", [])
        sources = snapshot.get("sources", [])
        if len(dependencies) != len(sources):
            raise ValueError("Missing signed source dependency")
        for source, dependency in zip(sources, dependencies):
            if (source["authorityDid"], source["recordId"], source["revision"]) != (
                dependency["authorityDid"], dependency["id"], dependency["revision"]):
                raise ValueError("Pinned dependency mismatch")
            if source.get("digest") and source["digest"] != digest_json(dependency):
                raise ValueError("Signed dependency digest mismatch")
            await verify_snapshot(dependency, visited | {key}, verification=verification)
            if snapshot["kind"] in {"offering", "supply"} and snapshot["ifcClass"] != dependency["ifcClass"]:
                raise ValueError("Signed source IFC class mismatch")
        if snapshot["kind"] == "supply":
            quantity = snapshot["data"].get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0 or not snapshot["data"].get("unit"):
                raise ValueError("Invalid signed supply quantity/unit")
        if snapshot.get("acceptedFrom"):
            provenance = snapshot["acceptedFrom"]
            original, issue, decision = provenance["snapshot"], provenance["issue"], provenance["decision"]
            await verify(original, original["authorityDid"], documents=verification["documents"])
            await verify(issue, issue["senderDid"], documents=verification["documents"])
            await verify(decision, decision["actorDid"], "capabilityInvocation", documents=verification["documents"])
            if (decision["actorDid"] != snapshot["authorityDid"] or issue["recipientDid"] != snapshot["authorityDid"]
                or issue["senderDid"] != original["authorityDid"] or decision["decision"] != "accept"
                or decision["submissionId"] != issue["submissionId"] or decision["issueManifestDigest"] != digest_json(issue)
                or original["id"] not in decision["recordIds"]
                or issue["recordDigests"].get(original["id"]) != digest_json(original)):
                raise ValueError("Acceptance provenance does not bind to its original signed issue/decision")
            await verify_snapshot(original, visited | {key}, verification=verification)
        for item in snapshot.get("documents", []):
            if "content" in item and hashlib.sha256(base64.b64decode(item["content"], validate=True)).hexdigest() != item["digest"]:
                raise ValueError("Public document digest mismatch")
        verification["snapshots"].add(snapshot_digest)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, ValidationError, binascii.Error) as error:
        raise HTTPException(424, f"Invalid signed source revision: {error}") from error


async def cache_snapshot(session, snapshot, *, public=False):
    if public and (snapshot.get("status") != "published" or snapshot.get("projectId") or
        snapshot.get("kind") not in {"product", "offering"} or
        any(item.get("visibility") != "public" for item in snapshot.get("documents", []))):
        raise HTTPException(424, "Public catalogue contains a private dependency/evidence")
    for dependency in snapshot.get("dependencies", []):
        await cache_snapshot(session, dependency, public=public)
    key = (snapshot["authorityDid"], snapshot["id"], snapshot["revision"])
    old = await session.get(SupplyChainRevision, key)
    if old and digest_json(old.snapshot_json) != digest_json(snapshot):
        raise HTTPException(409, "Authority revision equivocation")
    if old and public and not old.public:
        old.public = True
    if not old:
        session.add(SupplyChainRevision(authority_did=key[0], record_id=key[1], revision=key[2],
            public=public, snapshot_json=snapshot))
        await session.flush()


async def replay(session, key, body):
    row = await session.get(SupplyChainOperation, key)
    if row:
        if row.request_digest != digest_json(body):
            raise HTTPException(409, "Idempotency key reused for different content")
        return row.response_json
    return None


def remember(session, key, body, result):
    session.add(SupplyChainOperation(operation_key=key, request_digest=digest_json(body), response_json=copy.deepcopy(result)))


async def catalogue(session):
    rows = (await session.execute(select(SupplyChainRevision).where(
        SupplyChainRevision.authority_did == authority(), SupplyChainRevision.public.is_(True)))).scalars()
    return {"authorityDid": authority(), "items": [row.snapshot_json for row in rows]}


async def project_access(session, project_id, actor):
    policy = await session.get(ClipProjectPolicy, (authority(), project_id))
    if not policy:
        raise HTTPException(404, "Unknown recipient project")
    senders = await session.get(SupplyChainSenderPolicy, project_id)
    if actor != authority() and actor not in policy.members and not (senders and actor in senders.senders):
        raise HTTPException(403, "Organisation is neither a project member nor an approved scoped submission sender")
    return policy


async def create_submission(session, body):
    operation = "submission:create:" + body["idempotencyKey"]
    old = await replay(session, operation, body)
    if old:
        return old
    recipient = body["recipientDid"]
    if recipient == authority():
        await project_access(session, body["projectId"], authority())
    elif not await session.get(SupplyChainProject, (recipient, body["projectId"])):
        raise HTTPException(424, "Connect to the recipient project before creating a submission")
    records = [await record(session, item) for item in body["recordIds"]]
    if len(records) != len({row.record_id for row in records}) or not records:
        raise HTTPException(422, "Select distinct owned records")
    if body.get("supersedes"):
        prior = await session.get(SupplyChainSubmission, body["supersedes"])
        if not prior or prior.direction != "outgoing" or prior.submission_json["status"] == "draft" or (
            prior.submission_json["recipientDid"], prior.submission_json["projectId"]) != (recipient, body["projectId"]):
            raise HTTPException(422, "Correction must reference a previously issued submission to the same recipient/project")
    value = {"id": str(uuid4()), "senderDid": authority(), "recipientDid": recipient,
        "projectId": body["projectId"], "recordIds": body["recordIds"],
        "recordRevisions": {row.record_id: row.revision for row in records},
        "documentIds": body.get("documentIds", []), "supersedes": body.get("supersedes"),
        "revision": 1, "status": "draft", "direction": "outgoing"}
    session.add(SupplyChainSubmission(submission_id=value["id"], direction="outgoing", revision=1, submission_json=value))
    remember(session, operation, body, value)
    await session.commit()
    return value


async def submission(session, submission_id):
    row = await session.get(SupplyChainSubmission, submission_id)
    if row is None:
        raise HTTPException(404, "Unknown submission")
    return row


async def submission_precondition(session, row, expected):
    result = await session.execute(update(SupplyChainSubmission).where(
        SupplyChainSubmission.submission_id == row.submission_id, SupplyChainSubmission.revision == expected,
    ).values(revision=expected + 1))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "Submission changed; refresh before deciding")
    row.revision = expected + 1


async def issue_submission(session, row, body):
    operation = "submission:issue:" + row.submission_id + ":" + body["idempotencyKey"]
    old = await replay(session, operation, body)
    if old and row.submission_json.get("deliveryStatus") == "delivered":
        return old
    value = copy.deepcopy(row.submission_json)
    if row.direction != "outgoing":
        raise HTTPException(403, "Cannot issue another authority's submission")
    if value["status"] == "draft":
        await submission_precondition(session, row, body["expectedRevision"])
        records = [await record(session, item) for item in value["recordIds"]]
        if any(item.revision != value["recordRevisions"][item.record_id] for item in records):
            raise HTTPException(409, "Draft record revision changed; create a new reviewed submission")
        snapshots = [await freeze(session, item) for item in records]
        granted = []
        for document_id in value["documentIds"]:
            document = await session.get(SupplyChainDocument, document_id)
            if not document or document.record_id not in value["recordIds"]:
                raise HTTPException(424, "Selected evidence is unavailable or not owned by the submitted records")
            granted.append(document.metadata_json)
        manifest = sign({"profile": "urn:clip:supply-chain:issue-manifest:v1",
            "submissionId": value["id"], "senderDid": authority(), "recipientDid": value["recipientDid"],
            "projectId": value["projectId"], "supersedes": value["supersedes"],
            "recordDigests": {item["id"]: digest_json(item) for item in snapshots},
            "documentDigests": {item["id"]: digest_json(item) for item in granted},
            "created": timestamp()})
        issue = sign({"profile": "urn:clip:supply-chain:submission:v1",
            "submissionId": value["id"], "senderDid": authority(), "recipientDid": value["recipientDid"],
            "projectId": value["projectId"], "supersedes": value["supersedes"],
            "records": snapshots, "documents": granted, "manifest": manifest, "created": timestamp()})
        local_verify(issue, "assertionMethod")
        value.update(status="issued", revision=row.revision, issue=issue, deliveryStatus="pending")
        row.submission_json = value
        remember(session, operation, body, value)
        # Persist before egress: retries deliver exactly the same immutable issue.
        await session.commit()
        # JSON values aren't mutation-tracked: detach before setting deliveryStatus.
        value = copy.deepcopy(value)
    elif value["status"] not in {"issued", "accepted", "rejected", "changes-requested"}:
        raise HTTPException(409, "Submission cannot be issued")
    if value.get("deliveryStatus") != "delivered":
        if value["recipientDid"] == authority():
            received = await receive_issue(session, value["issue"], authority(), local=True)
            value = copy.deepcopy(received)
        else:
            await remote(value["recipientDid"], "issue", {"issue": value["issue"]})
        value["deliveryStatus"] = "delivered"
        row.submission_json = value
    operation_row = await session.get(SupplyChainOperation, operation)
    if operation_row:
        operation_row.response_json = copy.deepcopy(value)
    else:
        remember(session, operation, body, value)
    await session.commit()
    return value


async def receive_issue(session, issue, sender, *, local=False):
    try:
        if issue["senderDid"] != sender or issue["recipientDid"] != authority():
            raise HTTPException(403, "Submission sender/recipient mismatch")
        if issue.get("profile") != "urn:clip:supply-chain:submission:v1":
            raise HTTPException(422, "Unsupported submission profile")
        await project_access(session, issue["projectId"], sender)
        if not local:
            await verify(issue, sender)
            await verify(issue["manifest"], sender)
        snapshots = issue["records"]
        if not snapshots or len(snapshots) > 32 or len({item["id"] for item in snapshots}) != len(snapshots):
            raise HTTPException(422, "Issue must contain distinct records")
        manifest = issue["manifest"]
        if any(manifest.get(field) != issue.get(field) for field in ("submissionId", "senderDid", "recipientDid", "projectId", "supersedes")):
            raise HTTPException(424, "Signed issue manifest addressing mismatch")
        if manifest["recordDigests"] != {item["id"]: digest_json(item) for item in snapshots} or manifest["documentDigests"] != {
            item["id"]: digest_json(item) for item in issue.get("documents", [])}:
            raise HTTPException(424, "Signed issue manifest does not match the immutable records/evidence")
        documents = issue.get("documents", [])
        if len(documents) > 100 or len({item["id"] for item in documents}) != len(documents) or any(
            item["authorityDid"] != sender or item["recordId"] not in manifest["recordDigests"] for item in documents):
            raise HTTPException(422, "Issue evidence must be distinct attachments owned by its sender and selected records")
        verification = {"documents": {}, "snapshots": set()}
        for snapshot in snapshots:
            if snapshot["authorityDid"] != sender:
                raise HTTPException(403, "Sender may issue only its own records; source proofs must remain dependencies")
            if not local:
                await verify_snapshot(snapshot, verification=verification)
        old = await session.get(SupplyChainSubmission, issue["submissionId"])
        if old and not (local and old.direction == "outgoing"):
            if digest_json(old.submission_json["issue"]) != digest_json(issue):
                raise HTTPException(409, "Submission ID reused for different immutable issue")
            return old.submission_json
        supersedes = issue.get("supersedes")
        if supersedes:
            prior = await session.get(SupplyChainSubmission, supersedes)
            if not prior or (prior.submission_json["senderDid"], prior.submission_json["projectId"]) != (sender, issue["projectId"]):
                raise HTTPException(424, "Correction references an unavailable/different upstream issue")
        for snapshot in snapshots:
            await cache_snapshot(session, snapshot)
        value = {"id": issue["submissionId"], "senderDid": sender, "recipientDid": authority(),
            "projectId": issue["projectId"], "revision": 1, "status": "issued",
            "direction": "incoming", "issue": issue, "recordIds": [item["id"] for item in snapshots],
            "documentIds": [item["id"] for item in issue.get("documents", [])], "supersedes": supersedes}
        if local:
            # The same-node demo uses one durable issue with both inbox/outbox visibility.
            value["direction"] = "local"
            old.submission_json = {**old.submission_json, "localRecipient": True}
            await session.commit()
            return old.submission_json
        session.add(SupplyChainSubmission(submission_id=value["id"], direction="incoming", revision=1, submission_json=value))
        await session.commit()
        return value
    except KeyError as error:
        raise HTTPException(422, f"Malformed submission: missing {error}") from error


async def decide_submission(session, row, body):
    operation = "submission:decision:" + row.submission_id + ":" + body["idempotencyKey"]
    old = await replay(session, operation, body)
    if old:
        if row.submission_json.get("decisionDeliveryStatus") == "pending":
            await deliver_decision(session, row)
            return row.submission_json
        return row.submission_json
    value = copy.deepcopy(row.submission_json)
    if row.direction != "incoming" and not value.get("localRecipient"):
        raise HTTPException(403, "Only the named recipient can decide this submission")
    if value["status"] != "issued":
        raise HTTPException(409, "Issued submission already has an immutable decision")
    await project_access(session, value["projectId"], authority())
    await submission_precondition(session, row, body["expectedRevision"])
    selected = body.get("recordIds")
    if selected is None:
        selected = value["recordIds"]
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(value["recordIds"]):
        raise HTTPException(422, "Decision scope must select distinct records from the issue")
    decision = sign({"submissionId": value["id"], "issueDigest": digest_json(value["issue"]),
        "issueManifestDigest": digest_json(value["issue"]["manifest"]),
        "actorDid": authority(), "recipientDid": value["senderDid"], "projectId": value["projectId"],
        "decision": body["decision"], "recordIds": selected, "reason": body.get("reason", ""),
        "created": timestamp()}, "capabilityInvocation")
    local_verify(decision, "capabilityInvocation")
    status = {"accept": "accepted", "reject": "rejected", "request-changes": "changes-requested"}[body["decision"]]
    value.update(status=status, revision=row.revision, decision=decision, acceptedRecords=[],
        decisionDeliveryStatus="pending")
    row.submission_json = value
    if body["decision"] == "accept":
        dataset = await workspace(session, value["projectId"])
        accepted = []
        for snapshot in value["issue"]["records"]:
            if snapshot["id"] not in selected:
                continue
            kind = snapshot["kind"] if snapshot["kind"] in {"supply", "product", "offering"} else "asset"
            accepted_value = {"profile": RECORD_PROFILE, "id": str(uuid4()), "authorityDid": authority(), "kind": kind,
                "name": snapshot["name"], "projectId": value["projectId"], "ifcClass": snapshot["ifcClass"],
                "data": copy.deepcopy(snapshot["data"]), "sources": copy.deepcopy(snapshot["sources"]) if kind == "supply" else
                    [{"authorityDid": snapshot["authorityDid"], "recordId": snapshot["id"], "revision": snapshot["revision"]}],
                "documents": copy.deepcopy(snapshot["documents"]), "revision": 1, "status": "accepted",
                "datasetId": dataset.dataset_id, "graphPath": "supply-chain/" + str(uuid4()),
                "lineage": snapshot.get("lineage", []) + [{"authorityDid": snapshot["authorityDid"],
                    "recordId": snapshot["id"], "revision": snapshot["revision"], "kind": snapshot["kind"]}],
                "acceptedFrom": {"snapshot": snapshot, "issue": value["issue"]["manifest"], "decision": decision},
                "originalKind": snapshot["kind"]}
            attached_ids = {item["id"] for item in accepted_value["documents"]}
            accepted_value["documents"].extend(copy.deepcopy(item) for item in value["issue"].get("documents", [])
                if item["recordId"] == snapshot["id"] and item["id"] not in attached_ids)
            session.add(SupplyChainRecord(record_id=accepted_value["id"], authority_did=authority(), kind=kind,
                project_id=value["projectId"], revision=1, record_json=accepted_value))
            accepted.append(accepted_value)
        await session.flush()
        for accepted_value in accepted:
            await freeze(session, await session.get(SupplyChainRecord, accepted_value["id"]))
        value["acceptedRecords"] = accepted
        row.submission_json = value
        remember(session, operation, body, value)
        await graph_commit(session, dataset, accepted, create=True)
    else:
        remember(session, operation, body, value)
        await session.commit()
    await deliver_decision(session, row)
    return row.submission_json


async def deliver_decision(session, row):
    value = copy.deepcopy(row.submission_json)
    if value["senderDid"] != authority():
        await remote(value["senderDid"], "decision", {"decision": value["decision"]})
    value["decisionDeliveryStatus"] = "delivered"
    row.submission_json = value
    await session.commit()


async def receive_decision(session, decision, actor):
    row = await submission(session, decision.get("submissionId"))
    value = copy.deepcopy(row.submission_json)
    if row.direction != "outgoing" or value["recipientDid"] != actor:
        raise HTTPException(403, "Only the issue's recipient may decide it")
    await verify(decision, actor, "capabilityInvocation")
    if decision.get("issueDigest") != digest_json(value["issue"]) or decision.get("projectId") != value["projectId"] or decision.get("recipientDid") != authority():
        raise HTTPException(409, "Decision does not bind to the immutable issue")
    if decision.get("issueManifestDigest") != digest_json(value["issue"]["manifest"]):
        raise HTTPException(409, "Decision does not bind to the scoped immutable issue manifest")
    if not set(decision.get("recordIds", [])) <= set(value["recordIds"]) or not decision.get("recordIds"):
        raise HTTPException(422, "Decision scope is outside the issued records")
    if value.get("decision"):
        if digest_json(value["decision"]) != digest_json(decision):
            raise HTTPException(409, "Recipient already issued a different decision")
        return value
    statuses = {"accept": "accepted", "reject": "rejected", "request-changes": "changes-requested"}
    if decision.get("decision") not in statuses:
        raise HTTPException(422, "Unknown decision")
    await submission_precondition(session, row, row.revision)
    value.update(status=statuses[decision["decision"]], decision=decision, revision=row.revision)
    row.submission_json = value
    await session.commit()
    return value


async def authorised_document(session, document_id, actor=None):
    document = await session.get(SupplyChainDocument, document_id)
    if document is None:
        raise HTTPException(404, "Document bytes are not stored at this authority")
    if actor and actor != authority():
        grant = await session.get(SupplyChainDocumentGrant, (document_id, actor))
        allowed = bool(grant and grant.grant_json["active"])
        if allowed and grant.grant_json.get("expiresAt"):
            allowed = datetime.fromisoformat(grant.grant_json["expiresAt"].replace("Z", "+00:00")) > now()
        if grant and not allowed:
            raise HTTPException(403, "Source evidence grant is revoked or expired")
        submissions = (await session.execute(select(SupplyChainSubmission).where(SupplyChainSubmission.direction == "outgoing"))).scalars()
        for row in submissions:
            value = row.submission_json
            if value.get("issue") and value["recipientDid"] == actor and document_id in value["documentIds"]:
                allowed = True
        if not allowed:
            raise HTTPException(403, "Source evidence was not explicitly disclosed to this organisation")
    return document, box().decrypt(document.ciphertext)


async def grant_document(session, row, document_id, body):
    document = await session.get(SupplyChainDocument, document_id)
    if not document or document.record_id != row.record_id:
        raise HTTPException(403, "Only the original document authority can grant its evidence")
    recipient = body["recipientDid"]
    grant = await session.get(SupplyChainDocumentGrant, (document_id, recipient))
    revision = grant.grant_json["revision"] if grant else 0
    if body["expectedRevision"] != revision:
        raise HTTPException(409, "Evidence grant changed; refresh before saving")
    expires = body.get("expiresAt")
    if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) <= now():
        raise HTTPException(422, "Evidence grant expiry must be in the future")
    value = sign({"documentId": document_id, "publisherDid": authority(), "recipientDid": recipient,
        "active": body["active"], "expiresAt": expires, "revision": revision + 1, "created": timestamp()})
    if grant:
        from sqlalchemy import update
        result = await session.execute(update(SupplyChainDocumentGrant).where(
            SupplyChainDocumentGrant.document_id == document_id, SupplyChainDocumentGrant.recipient_did == recipient,
            SupplyChainDocumentGrant.grant_json["revision"].as_integer() == body["expectedRevision"]).values(grant_json=value))
        if result.rowcount != 1:
            raise HTTPException(409, "Evidence grant changed concurrently")
    else:
        session.add(SupplyChainDocumentGrant(document_id=document_id, recipient_did=recipient, grant_json=value))
    from sqlalchemy.exc import IntegrityError
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, "Evidence grant changed concurrently") from error
    return value
