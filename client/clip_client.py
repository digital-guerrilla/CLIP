"""CLIP network and IFC graph SDK; service identity is DID-based, not an API key."""

import base64
from datetime import datetime, timezone
import hashlib
from urllib.parse import quote
from uuid import uuid4

import httpx
import nacl.public
import nacl.signing
import rfc8785

from node.app.core.content_crypto import EncryptedFragment, EncryptedManifest, decrypt_fragments
from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.core.did import resolve_did_web_document, verify_clip_message_proof
from node.app.core.ifc_protocol import (
    IfcProposalTransaction,
    IfcGraphProposalTransaction,
    IfcDecisionTransaction,
)


class CLIPClient:
    def __init__(self, node_url: str, api_key: str | None = None, timeout: float = 15):
        self.base = node_url.rstrip("/")
        self.api_key = api_key
        self.http = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self.http.aclose()

    async def _request(self, method: str, path: str, *, body=None, private=False, params=None):
        if private and not self.api_key:
            raise ValueError("A local API key is required for this operation")
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        response = await self.http.request(method, self.base + path, json=body, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_node_info(self):
        return await self._request("GET", "/clip/v1/node/info")

    async def get_did_document(self):
        return await self._request("GET", "/.well-known/did.json")

    async def list_datasets(self):
        return (await self._request("GET", "/ifc/v1/datasets"))["items"]

    async def register_dataset(self, file: dict, trusted_proposers: list[str]):
        return await self._request("POST", "/ifc/v1/datasets", body={"file": file, "trustedProposers": trusted_proposers}, private=True)

    async def resolve_graph(self, dataset_id: str, *, product_view: str = "current", refresh_products: bool = False):
        return await self._request("GET", f"/ifc/v1/datasets/{quote(dataset_id, safe='')}/graph",
            params={"product_view": product_view, "refresh_products": refresh_products},
            private=refresh_products)

    async def resolve_component(self, target: dict, *, product_view: str = "current"):
        document = await self.get_did_document()
        if target["authorityDid"] != document["id"]:
            raise ValueError("The component address belongs to another authority")
        result = await self._request("GET", f"/ifc/v1/datasets/{quote(target['datasetId'], safe='')}/components", params={
            "entity_path": target["entityPath"], "component_schema_id": target["componentSchemaId"],
            "product_view": product_view})
        return result["value"]

    async def get_history(self, dataset_id: str):
        return (await self._request("GET", f"/ifc/v1/datasets/{quote(dataset_id, safe='')}/history"))["items"]

    async def list_projects(self) -> list[dict]:
        return (await self._request("GET", "/clip/v1/projects", private=True))["items"]

    async def create_project(self, name: str, *, kind: str = "project", visibility: str = "private") -> dict:
        return await self._request("POST", "/clip/v1/projects",
            body={"name": name, "kind": kind, "visibility": visibility}, private=True)

    async def get_entity_templates(self) -> dict:
        return await self._request("GET", "/ifc/v1/projects/templates")

    async def create_entity(self, project_id: str, name: str, template: str, *,
                            parent_path: str | None = None, type_path: str | None = None) -> dict:
        return await self._request("POST", f"/ifc/v1/projects/{quote(project_id, safe='')}/entities",
            body={"name": name, "template": template, "parentPath": parent_path, "typePath": type_path},
            private=True)

    async def set_project_permissions(self, project_id: str, members: dict[str, str], *,
                                      expected_revision: int, visibility: str = "private") -> dict:
        return await self._request("PUT", f"/clip/v1/projects/{quote(project_id, safe='')}/permissions",
            body={"members": members, "visibility": visibility, "expectedRevision": expected_revision},
            private=True)

    async def create_project_invite(self, project_id: str, *, role: str = "viewer",
                                   expires_hours: int = 168) -> dict:
        return await self._request("POST", f"/clip/v1/projects/{quote(project_id, safe='')}/invites",
            body={"role": role, "expiresHours": expires_hours}, private=True)

    async def redeem_project_invite(self, code: str) -> dict:
        return await self._request("POST", "/clip/v1/projects/invites/redeem",
            body={"code": code}, private=True)

    async def list_project_join_requests(self, project_id: str) -> dict:
        return await self._request("GET", f"/clip/v1/projects/{quote(project_id, safe='')}/join-requests",
            private=True)

    async def decide_project_join(self, request_id: str, decision: str, *,
                                 expected_revision: int) -> dict:
        return await self._request("POST", f"/clip/v1/projects/join-requests/{quote(request_id, safe='')}/decision",
            body={"decision": decision, "expectedRevision": expected_revision}, private=True)

    async def list_workflow_records(self, *, kind: str | None = None,
                                    project_id: str | None = None) -> list[dict]:
        params = {}
        if kind is not None:
            params["kind"] = kind
        if project_id is not None:
            params["projectId"] = project_id
        return (await self._request("GET", "/clip/v1/supply-chain/records",
            params=params, private=True))["items"]

    async def create_workflow_record(self, record: dict) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/records",
            body=record, private=True)

    async def adopt_workflow_product(self, dataset_id: str, entity_path: str, *,
                                     expected_sequence: int, idempotency_key: str) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/records/adopt",
            body={"datasetId": dataset_id, "entityPath": entity_path,
                  "expectedSequence": expected_sequence, "idempotencyKey": idempotency_key},
            private=True)

    async def get_workflow_adoption_candidates(self) -> dict:
        return await self._request("GET", "/clip/v1/supply-chain/records/adoption-candidates", private=True)

    async def get_workflow_record(self, record_id: str) -> dict:
        return await self._request("GET",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}", private=True)

    async def update_workflow_record(self, record_id: str, record: dict) -> dict:
        return await self._request("PUT",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}",
            body=record, private=True)

    async def publish_workflow_record(self, record_id: str, publication: dict) -> dict:
        return await self._request("POST",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/publish",
            body=publication, private=True)

    async def list_workflow_revisions(self, record_id: str) -> list[dict]:
        return (await self._request("GET",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/revisions",
            private=True))["items"]

    async def freeze_workflow_revision(self, record_id: str, *, expected_revision: int) -> dict:
        return await self._request("POST",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/revisions",
            body={"expectedRevision": expected_revision}, private=True)

    async def get_workflow_schema(self) -> dict:
        return await self._request("GET", "/clip/v1/supply-chain/schema")

    async def preview_workflow_dependencies(self, sources: list[dict]) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/dependencies/preview",
            body={"sources": sources}, private=True)

    async def get_public_catalogue(self) -> dict:
        return await self._request("GET", "/clip/v1/supply-chain/catalogue")

    async def get_public_workflow_revision(self, record_id: str, revision: int) -> dict:
        return await self._request("GET",
            f"/clip/v1/supply-chain/catalogue/{quote(record_id, safe='')}/revisions/{revision}")

    async def discover_catalogue(self, authority_did: str) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/catalogue/discover",
            body={"authorityDid": authority_did}, private=True)

    async def list_workflow_projects(self) -> list[dict]:
        return (await self._request("GET", "/clip/v1/supply-chain/projects", private=True))["items"]

    async def connect_workflow_project(self, authority_did: str, project_id: str) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/projects/connect",
            body={"authorityDid": authority_did, "projectId": project_id}, private=True)

    async def refresh_workflow_project(self, authority_did: str, project_id: str) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/projects/refresh",
            body={"authorityDid": authority_did, "projectId": project_id}, private=True)

    async def get_workflow_senders(self, project_id: str) -> dict:
        return await self._request("GET",
            f"/clip/v1/supply-chain/projects/{quote(project_id, safe='')}/senders", private=True)

    async def set_workflow_senders(self, project_id: str, senders: list[str], *,
                                   expected_revision: int) -> dict:
        return await self._request("PUT",
            f"/clip/v1/supply-chain/projects/{quote(project_id, safe='')}/senders",
            body={"senders": senders, "expectedRevision": expected_revision}, private=True)

    async def upload_workflow_document(self, record_id: str, content: bytes, name: str, *,
                                       expected_revision: int, media_type: str = "application/octet-stream",
                                       visibility: str = "private", idempotency_key: str | None = None,
                                       replaces_document_id: str | None = None) -> dict:
        body = {"name": name, "mediaType": media_type, "content": base64.b64encode(content).decode("ascii"),
                "visibility": visibility, "expectedRevision": expected_revision}
        if idempotency_key is not None:
            body["idempotencyKey"] = idempotency_key
        if replaces_document_id is not None:
            body["replacesDocumentId"] = replaces_document_id
        return await self._request("POST",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/documents",
            body=body, private=True)

    async def detach_workflow_document(self, record_id: str, document_id: str, *,
                                       expected_revision: int, idempotency_key: str | None = None) -> dict:
        body: dict[str, str | int] = {"expectedRevision": expected_revision}
        if idempotency_key is not None:
            body["idempotencyKey"] = idempotency_key
        return await self._request("POST",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/documents/{quote(document_id, safe='')}/detach",
            body=body, private=True)

    async def list_workflow_documents(self, record_id: str) -> list[dict]:
        return (await self._request("GET",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/documents",
            private=True))["items"]

    async def get_workflow_document(self, record_id: str, document_id: str) -> bytes:
        if not self.api_key:
            raise ValueError("A local API key is required for this operation")
        response = await self.http.get(
            f"{self.base}/clip/v1/supply-chain/records/{quote(record_id, safe='')}/documents/{quote(document_id, safe='')}",
            headers={"x-api-key": self.api_key})
        response.raise_for_status()
        return response.content

    async def get_workflow_document_grants(self, record_id: str, document_id: str) -> list[dict]:
        return (await self._request("GET",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/documents/{quote(document_id, safe='')}/grants",
            private=True))["items"]

    async def grant_workflow_document(self, record_id: str, document_id: str, recipient_did: str, *,
                                      expected_revision: int = 0, active: bool = True,
                                      expires_at: str | None = None) -> dict:
        return await self._request("POST",
            f"/clip/v1/supply-chain/records/{quote(record_id, safe='')}/documents/{quote(document_id, safe='')}/grants",
            body={"recipientDid": recipient_did, "expectedRevision": expected_revision,
                  "active": active, "expiresAt": expires_at}, private=True)

    async def list_submissions(self, *, direction: str | None = None,
                               project_id: str | None = None) -> list[dict]:
        params = {}
        if direction is not None:
            params["direction"] = direction
        if project_id is not None:
            params["projectId"] = project_id
        return (await self._request("GET", "/clip/v1/supply-chain/submissions",
            params=params, private=True))["items"]

    async def create_submission(self, submission: dict) -> dict:
        return await self._request("POST", "/clip/v1/supply-chain/submissions",
            body=submission, private=True)

    async def get_submission(self, submission_id: str) -> dict:
        return await self._request("GET",
            f"/clip/v1/supply-chain/submissions/{quote(submission_id, safe='')}", private=True)

    async def issue_submission(self, submission_id: str, issue: dict) -> dict:
        return await self._request("POST",
            f"/clip/v1/supply-chain/submissions/{quote(submission_id, safe='')}/issue",
            body=issue, private=True)

    async def decide_submission(self, submission_id: str, decision: dict) -> dict:
        return await self._request("POST",
            f"/clip/v1/supply-chain/submissions/{quote(submission_id, safe='')}/decision",
            body=decision, private=True)

    @staticmethod
    def sign_transaction(transaction: dict, key: NodeKeyManager, verification_method: str, *, decision=False):
        now = datetime.now(timezone.utc)
        purpose = "capabilityInvocation" if decision else "assertionMethod"
        model = (IfcDecisionTransaction if decision else
                 IfcGraphProposalTransaction if "operations" in transaction else IfcProposalTransaction)
        unsigned = {"transactionId": str(uuid4()), "created": now.isoformat().replace("+00:00", "Z"), **transaction}
        normalized = model.model_validate({**unsigned, "proof": {
            "type": "DataIntegrityProof", "cryptosuite": "eddsa-jcs-2022", "created": now,
            "verificationMethod": verification_method, "proofPurpose": purpose, "proofValue": "z" + "1" * 86,
        }}).model_dump(mode="json", by_alias=True, exclude={"proof"})
        return add_data_integrity_proof(normalized, key.private_key_bytes, verification_method=verification_method, proof_purpose=purpose, created=now)

    async def propose(self, signed_proposal: dict):
        return await self._request("POST", "/ifc/v1/proposals", body=signed_proposal)

    async def decide(self, signed_decision: dict):
        receipt = await self._request("POST", "/ifc/v1/decisions", body=signed_decision)
        if not verify_clip_message_proof(receipt, await self.get_did_document()):
            raise ValueError("Authority returned an invalid receipt")
        return receipt

    async def get_gossip_peers(self):
        return await self._request("GET", "/clip/v1/network/gossip/peers", private=True)

    async def get_replication_status(self):
        return await self._request("GET", "/clip/v1/replication/status", private=True)

    async def replicate(self, dataset_id: str, peer_did: str):
        return await self._request("POST", "/clip/v1/replication/push", body={"datasetId": dataset_id, "peerDid": peer_did}, private=True)

    async def import_construction(self, dataset_id: str, content: str, *, source_format="cobie", type_content=""):
        if source_format not in {"cobie", "ifc43"}:
            raise ValueError("source_format must be cobie or ifc43")
        return await self._request("POST", f"/ifc/v1/imports/{source_format}", body={"datasetId": dataset_id, "content": content, "typeContent": type_content}, private=True)

    async def upload_evidence(self, target: dict, content: bytes, name: str, recipients: list[str], *, retention_seconds: int, media_type="application/octet-stream"):
        return await self._request("POST", "/clip/v1/evidence/upload", body={"target": target, "content": base64.b64encode(content).decode("ascii"), "name": name,
            "recipients": recipients, "retentionSeconds": retention_seconds, "mediaType": media_type}, private=True)

    async def replicate_evidence(self, evidence_id: str, peer_did: str):
        return await self._request("POST", f"/clip/v1/evidence/{evidence_id}/replicate", body={"peerDid": peer_did}, private=True)

    async def repair_evidence(self, evidence_id: str, peer_did: str):
        return await self._request("POST", f"/clip/v1/evidence/{evidence_id}/repair", body={"peerDid": peer_did}, private=True)

    async def decrypt_evidence(self, reference: dict, recipient_did: str, recipient_key: NodeKeyManager):
        if not self.api_key:
            raise ValueError("A local API key is required for this operation")
        evidence_id = reference["evidenceId"]
        signed = await self._request("GET", f"/clip/v1/evidence/{evidence_id}", private=True)
        if hashlib.sha256(rfc8785.dumps(signed)).hexdigest() != reference["integrity"] or signed["publisherDid"] != reference["publisherDid"]:
            raise ValueError("Evidence reference does not match the manifest")
        document = await resolve_did_web_document(signed["publisherDid"])
        if not verify_clip_message_proof(signed, document):
            raise ValueError("Invalid evidence manifest proof")
        manifest = signed["manifest"]
        envelope = next((item for item in manifest["recipients"] if item["did"] == recipient_did), None)
        if envelope is None:
            raise ValueError("Recipient has no document key envelope")
        curve_key = nacl.signing.SigningKey(recipient_key.private_key_bytes).to_curve25519_private_key()
        key = nacl.public.SealedBox(curve_key).decrypt(base64.b64decode(envelope["wrappedKey"]))
        fragments = []
        for item in manifest["fragments"]:
            response = await self.http.get(f"{self.base}/clip/v1/evidence/{evidence_id}/fragments/{item['index']}", headers={"x-api-key": self.api_key})
            response.raise_for_status()
            content = response.content
            if hashlib.sha256(content).hexdigest() != item["digest"]:
                raise ValueError("Encrypted fragment does not match the signed manifest")
            fragments.append(EncryptedFragment(item["index"], content[:24], content[24:], item["sha256"]))
        return decrypt_fragments(key, EncryptedManifest(manifest["contentSha256"], manifest["encryptedContentSha256"], manifest["chunkSize"], manifest["fragmentCount"], tuple(manifest["fragments"])), fragments)