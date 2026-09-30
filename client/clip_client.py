"""Async client for CLIP v3 nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass
class RecordResult:
    id: str
    authority: str
    controller: str
    record_kind: str
    subject: dict[str, Any]
    relationships: list[dict[str, Any]]
    availability: dict[str, Any]
    version: int
    created_at: str
    updated_at: str
    proof: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.subject.get("name", ""))


@dataclass
class NodeInfo:
    routing_host: str
    authority: str
    protocol_version: str
    supported_record_kinds: list[str] = field(default_factory=list)
    role: str = "resolver"


class CLIPClient:
    def __init__(self, node_url: str, api_key: str | None = None, timeout: int = 15):
        self._base = node_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "CLIPClient":
        self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        return self._client

    def _headers(self, write: bool = False) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if write:
            if not self._api_key:
                raise ValueError("api_key is required for write operations")
            headers["x-api-key"] = self._api_key
        return headers

    async def get_node_info(self) -> NodeInfo:
        response = await self._http().get(f"{self._base}/v3/node/info")
        response.raise_for_status()
        return NodeInfo(**response.json())

    async def get_storage_status(self) -> dict[str, Any]:
        response = await self._http().get(
            f"{self._base}/v3/node/storage",
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def get_well_known(self) -> dict[str, Any]:
        response = await self._http().get(f"{self._base}/.well-known/clip/server")
        response.raise_for_status()
        return response.json()

    async def get_record(self, clip: str) -> RecordResult:
        authority, record_uuid = _split_clip(clip)
        response = await self._http().get(
            f"{self._base}/v3/records/{authority}/{record_uuid}"
        )
        response.raise_for_status()
        return _parse_record(response.json())

    async def list_records(self, limit: int = 50, offset: int = 0) -> list[RecordResult]:
        response = await self._http().get(
            f"{self._base}/v3/records",
            params={"limit": limit, "offset": offset},
        )
        response.raise_for_status()
        return [_parse_record(item) for item in response.json()["items"]]

    async def create_record(
        self,
        record_kind: str,
        subject: dict[str, Any],
        *,
        controller: str | None = None,
        availability: dict[str, Any] | None = None,
    ) -> RecordResult:
        payload: dict[str, Any] = {"record_kind": record_kind, "subject": subject}
        if controller:
            payload["controller"] = controller
        if availability:
            payload["availability"] = availability
        response = await self._http().post(
            f"{self._base}/v3/records",
            json=payload,
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return _parse_record(response.json())

    async def update_record(
        self,
        record: RecordResult,
        subject: dict[str, Any],
    ) -> RecordResult:
        authority, record_uuid = _split_clip(record.id)
        response = await self._http().put(
            f"{self._base}/v3/records/{authority}/{record_uuid}",
            json={
                "subject": subject,
                "controller": record.controller,
                "relationships": record.relationships,
                "availability": record.availability,
            },
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return _parse_record(response.json())

    async def get_history(self, clip: str) -> list[dict[str, Any]]:
        authority, record_uuid = _split_clip(clip)
        response = await self._http().get(
            f"{self._base}/v3/records/{authority}/{record_uuid}/history"
        )
        response.raise_for_status()
        return response.json()

    async def upload_encrypted_document(
        self,
        clip: str,
        content: bytes,
        file_name: str,
        *,
        media_type: str = "application/octet-stream",
        chunk_size: int = 1024 * 1024,
    ) -> dict[str, Any]:
        authority, record_uuid = _split_clip(clip)
        response = await self._http().post(
            f"{self._base}/v3/documents/encrypted-upload/{authority}/{record_uuid}",
            headers={
                **self._headers(write=True),
                "x-file-name": file_name,
                "content-type": media_type,
                "x-chunk-size": str(chunk_size),
            },
            content=content,
        )
        response.raise_for_status()
        return response.json()

    async def get_encrypted_document(self, sha256: str, encryption_key: str) -> bytes:
        response = await self._http().get(
            f"{self._base}/v3/documents/encrypted/{sha256}",
            headers={
                **self._headers(write=True),
                "x-document-key": encryption_key,
            },
        )
        response.raise_for_status()
        return response.content

    async def resolve_graph(
        self,
        root: str,
        *,
        depth: int = 2,
        max_nodes: int = 50,
        view: str = "public",
    ) -> dict[str, Any]:
        response = await self._http().post(
            f"{self._base}/v3/resolve-graph",
            json={"root": root, "depth": depth, "max_nodes": max_nodes, "view": view},
        )
        response.raise_for_status()
        return response.json()

    async def import_assets(
        self,
        content: str,
        *,
        source_format: str = "csv",
        field_mapping: dict[str, str] | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(write=True)
        if idempotency_key:
            headers["x-idempotency-key"] = idempotency_key
        response = await self._http().post(
            f"{self._base}/v3/imports/assets",
            json={
                "content": content,
                "source_format": source_format,
                "field_mapping": field_mapping or {},
                "dry_run": dry_run,
            },
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_import_status(self, import_id: str) -> dict[str, Any]:
        response = await self._http().get(
            f"{self._base}/v3/imports/{import_id}",
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def query_assets(self, **filters: Any) -> list[RecordResult]:
        response = await self._http().get(
            f"{self._base}/v3/records/query",
            params=filters,
            headers=self._headers(),
        )
        response.raise_for_status()
        return [_parse_record(item) for item in response.json()["items"]]

    async def propose_relationship(self, proposal: dict[str, Any]) -> dict[str, Any]:
        response = await self._http().post(
            f"{self._base}/v3/relationships/proposals",
            json=proposal,
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def accept_relationship(self, proposal: dict[str, Any]) -> dict[str, Any]:
        response = await self._http().post(
            f"{self._base}/v3/relationships/accept",
            json=proposal,
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def bulk_propose_relationships(self, proposals: list[dict[str, Any]]) -> dict[str, Any]:
        response = await self._http().post(
            f"{self._base}/v3/relationships/bulk-proposals",
            json={"proposals": proposals},
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def get_replication_status(self, clip: str) -> dict[str, Any]:
        authority, record_uuid = _split_clip(clip)
        response = await self._http().get(
            f"{self._base}/v3/replication/status/{authority}/{record_uuid}",
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def retry_replication(self, clip: str) -> dict[str, Any]:
        authority, record_uuid = _split_clip(clip)
        response = await self._http().post(
            f"{self._base}/v3/replication/retry/{authority}/{record_uuid}",
            headers=self._headers(write=True),
        )
        response.raise_for_status()
        return response.json()

    async def get_gossip_peers(self) -> list[dict[str, Any]]:
        response = await self._http().get(f"{self._base}/v3/gossip/peers")
        response.raise_for_status()
        return response.json()


def _split_clip(clip: str) -> tuple[str, str]:
    parsed = urlsplit(clip)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "clip" or not parsed.netloc or len(parts) != 2:
        raise ValueError(f"Invalid CLIP URI: {clip!r}")
    return parts[0], parts[1]


def _parse_record(document: dict[str, Any]) -> RecordResult:
    return RecordResult(
        id=document["id"],
        authority=document["authority"],
        controller=document["controller"],
        record_kind=document["record_kind"],
        subject=document["subject"],
        relationships=document.get("relationships", []),
        availability=document["availability"],
        version=document["version"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        proof=document["proof"],
    )