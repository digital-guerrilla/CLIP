"""Verified remote imports and layer federation for IFCX datasets."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlsplit

import httpx
import rfc8785

from ..core.did import (
    DidVerificationError,
    is_loopback_host,
    resolve_did_web_document,
    validate_public_host,
    verify_clip_message_proof,
)
from ..config import settings
from ..core.egress import public_http_client, decode_json
from ..core.ifc_graph import federate_ifc_layers
from ..core.ifcx_models import IfcxFile, IfcxSchema, validate_ifcx_attributes
from ..core.ifc_protocol import IfcPublicationEnvelope

ImportFetcher = Callable[[str], Awaitable[bytes]]
DidDocumentResolver = Callable[[str], Awaitable[dict[str, Any]]]
MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_DEPTH = 8
MAX_IMPORT_LAYERS = 32


class ClipFederationError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedIfcLayers:
    layers: tuple[IfcxFile, ...]
    federated_file: IfcxFile
    sources: tuple[dict[str, Any], ...] = ()


def sha256_sri_integrity(content: bytes) -> str:
    """Encode the CLIP IFCX import profile's SRI SHA-256 byte pin."""
    return "sha256-" + base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def verify_import_integrity(content: bytes, integrity: str | None) -> None:
    if not integrity or not integrity.startswith("sha256-"):
        raise ClipFederationError("Remote IFCX imports require a sha256 SRI integrity value")
    encoded_digest = integrity[len("sha256-"):]
    try:
        expected = base64.b64decode(encoded_digest, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ClipFederationError("Invalid sha256 SRI integrity value") from error
    if len(expected) != hashlib.sha256().digest_size:
        raise ClipFederationError("Invalid sha256 SRI digest length")
    if not hmac.compare_digest(hashlib.sha256(content).digest(), expected):
        raise ClipFederationError("Imported IFCX bytes do not match their integrity digest")


async def _fetch_https_import(uri: str) -> bytes:
    parsed = urlsplit(uri)
    loopback_exception = (
        settings.CLIP_ALLOW_HTTP_LOOPBACK
        and parsed.scheme == "http"
        and is_loopback_host(parsed.hostname or "")
    )
    if (
        (parsed.scheme != "https" and not loopback_exception)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ClipFederationError("IFCX imports must use HTTPS without user-info or fragments")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ClipFederationError("Invalid IFCX import URL port") from error
    if not loopback_exception:
        try:
            await validate_public_host(parsed.hostname, port)
        except DidVerificationError as error:
            raise ClipFederationError(str(error)) from error

    timeout = httpx.Timeout(8.0, connect=3.0)
    chunks: list[bytes] = []
    total_bytes = 0
    async with public_http_client(timeout) as client:
        async with client.stream(
            "GET",
            uri,
            headers={"Accept": "application/json, application/ifcx+json"},
        ) as response:
            if response.status_code != 200:
                raise ClipFederationError(
                    f'IFCX import "{uri}" returned HTTP {response.status_code}'
                )
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > MAX_IMPORT_BYTES:
                    raise ClipFederationError("Imported IFCX file exceeds the size limit")
                chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_pinned_import(uri: str, integrity: str) -> bytes:
    from ..db.database import AsyncSessionLocal
    from ..db.orm_models import ClipImportCache

    async with AsyncSessionLocal() as session:
        cached = await session.get(ClipImportCache, (uri, integrity))
        if cached is not None:
            fetched = cached.fetched_at
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            try:
                verify_import_integrity(cached.content, integrity)
                if (datetime.now(timezone.utc) - fetched).total_seconds() < settings.CACHE_TTL:
                    return cached.content
            except ClipFederationError:
                await session.delete(cached)
                await session.commit()
                cached = None
        content = await _fetch_https_import(uri)
        verify_import_integrity(content, integrity)
        if cached is None:
            session.add(ClipImportCache(uri=uri, integrity=integrity, content=content, fetched_at=datetime.now(timezone.utc)))
        else:
            cached.content = content
            cached.fetched_at = datetime.now(timezone.utc)
        await session.commit()
        return content


async def resolve_ifc_layers(
    root: IfcxFile,
    *,
    fetcher: ImportFetcher = _fetch_https_import,
    did_resolver: DidDocumentResolver = resolve_did_web_document,
    max_depth: int = MAX_IMPORT_DEPTH,
    max_layers: int = MAX_IMPORT_LAYERS,
    trusted_publishers: set[str] | None = None,
) -> ResolvedIfcLayers:
    """Fetch byte-pinned, DID-signed imports after the root layer, matching IFCX stack order."""
    content_cache: dict[str, bytes] = {}
    if trusted_publishers is None:
        trusted_publishers = {value.strip() for value in settings.CLIP_TRUSTED_PUBLISHERS.split(",") if value.strip()}
    file_cache: dict[str, IfcxFile] = {}
    source_cache: dict[str, dict[str, Any]] = {}
    expanded_uris: set[str] = set()
    dataset_payloads: dict[str, bytes] = {
        root.header.id: rfc8785.dumps(root.model_dump(mode="json", by_alias=True)),
    }

    async def load_uri(import_node, active_uris: frozenset[str]) -> IfcxFile:
        uri = import_node.uri
        if uri in active_uris:
            raise ClipFederationError(f'Cyclic IFCX import at "{uri}"')
        if uri not in file_cache:
            if not import_node.integrity:
                raise ClipFederationError(f'Import "{uri}" has no integrity pin')
            try:
                content = await _fetch_pinned_import(uri, import_node.integrity) if fetcher is _fetch_https_import else await fetcher(uri)
            except Exception as error:
                if isinstance(error, ClipFederationError):
                    raise
                raise ClipFederationError(f'Unable to fetch IFCX import "{uri}"') from error
            if len(content) > MAX_IMPORT_BYTES:
                raise ClipFederationError("Imported IFCX file exceeds the size limit")
            verify_import_integrity(content, import_node.integrity)
            try:
                decoded = decode_json(content)
                envelope = IfcPublicationEnvelope.model_validate(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ClipFederationError(
                    f'Invalid IFCX publication envelope "{uri}"'
                ) from error
            if envelope.publisher_did not in trusted_publishers:
                raise ClipFederationError(f'Untrusted IFCX publisher "{envelope.publisher_did}"')
            try:
                did_document = await did_resolver(envelope.publisher_did)
            except Exception as error:
                raise ClipFederationError(
                    f'Unable to resolve publisher DID "{envelope.publisher_did}"'
                ) from error
            if not verify_clip_message_proof(decoded, did_document):
                raise ClipFederationError(
                    f'Invalid IFCX publication proof from "{envelope.publisher_did}"'
                )
            imported_file = envelope.file
            canonical_payload = rfc8785.dumps(imported_file.model_dump(mode="json", by_alias=True))
            prior_payload = dataset_payloads.get(imported_file.header.id)
            if prior_payload is not None and prior_payload != canonical_payload:
                raise ClipFederationError(
                    f'Different files use the same IFCX dataset id "{imported_file.header.id}"'
                )
            dataset_payloads[imported_file.header.id] = canonical_payload
            content_cache[uri] = content
            file_cache[uri] = imported_file
            source_cache[uri] = {
                "publisherDid": envelope.publisher_did,
                "datasetId": imported_file.header.id,
                "uri": uri,
                "integrity": import_node.integrity,
                "entityPaths": list(dict.fromkeys(node.path for node in imported_file.data)),
            }
        else:
            verify_import_integrity(content_cache[uri], import_node.integrity)
        return file_cache[uri]

    async def visit(
        current: IfcxFile,
        *,
        depth: int,
        active_uris: frozenset[str],
        active_dataset_ids: frozenset[str],
    ) -> list[IfcxFile]:
        if depth > max_depth:
            raise ClipFederationError("IFCX import nesting exceeds the depth limit")
        if current.header.id in active_dataset_ids:
            raise ClipFederationError(f'Cyclic IFCX dataset import at "{current.header.id}"')

        layers: list[IfcxFile] = [current]
        next_active_ids = active_dataset_ids | {current.header.id}
        for import_node in current.imports:
            imported_file = await load_uri(import_node, active_uris)
            if import_node.uri in expanded_uris:
                continue
            imported_layers = await visit(
                imported_file,
                depth=depth + 1,
                active_uris=active_uris | {import_node.uri},
                active_dataset_ids=next_active_ids,
            )
            expanded_uris.add(import_node.uri)
            layers.extend(imported_layers)
            if len(layers) >= max_layers:
                raise ClipFederationError("IFCX import graph exceeds the layer limit")

        if len(layers) > max_layers:
            raise ClipFederationError("IFCX import graph exceeds the layer limit")
        return layers

    layers = await visit(
        root,
        depth=0,
        active_uris=frozenset(),
        active_dataset_ids=frozenset(),
    )
    federated_file = federate_ifc_layers(layers)
    validate_ifcx_attributes(federated_file)
    return ResolvedIfcLayers(tuple(layers), federated_file, tuple(source_cache.values()))