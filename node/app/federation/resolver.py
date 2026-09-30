"""Discovery and cryptographic verification for one CLIP v3 record."""

import httpx

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..core.guid import parse_clip
from ..core.models import AssetRecord, WellKnownResponse


def _discovery_scheme(routing_host: str) -> str:
    host = routing_host.split(":")[0].lower()
    local = host == "localhost" or host.replace(".", "").isdigit() or host.startswith("[")
    return "http" if local or settings.ALLOW_INSECURE_HTTP_DISCOVERY else "https"


async def fetch_well_known(routing_host: str, timeout: int = 10) -> WellKnownResponse:
    scheme = _discovery_scheme(routing_host)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            response = await client.get(f"{scheme}://{routing_host}/.well-known/clip/server")
            if response.status_code == 200:
                descriptor = WellKnownResponse.model_validate(response.json())
                method = descriptor.verification_methods[0]
                if (
                    descriptor.authority == descriptor.genesis_public_key_multibase
                    and method.public_key_multibase == descriptor.authority
                    and NodeKeyManager.verify(
                        descriptor.model_dump(mode="json"),
                        descriptor.proof,
                        method.public_key_base64,
                    )
                ):
                    return descriptor
        except (httpx.RequestError, ValueError):
            pass
    raise ValueError(f"Could not verify CLIP authority descriptor for {routing_host!r}")


async def resolve_clip(
    clip: str,
    timeout: int = 10,
) -> tuple[AssetRecord, str, str]:
    parsed = parse_clip(clip)
    descriptor = await fetch_well_known(parsed.routing_host, timeout)
    if descriptor.authority != parsed.authority_key_fingerprint:
        raise ValueError("Authority descriptor fingerprint does not match the requested CLIP")

    endpoint = descriptor.endpoints[0].rstrip("/")
    url = f"{endpoint}/v3/records/{parsed.authority_key_fingerprint}/{parsed.record_uuid}"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            response = await client.get(url, headers={"accept": "application/json"})
        except httpx.RequestError as exc:
            raise ValueError(f"Authority request failed: {exc}") from exc
    if response.status_code == 404:
        raise ValueError(f"Record not found: {clip}")
    if response.status_code != 200:
        raise ValueError(f"Authority returned HTTP {response.status_code}")

    record = AssetRecord.model_validate(response.json())
    if record.id != clip or record.authority != descriptor.authority:
        raise ValueError("Authority returned a record with a mismatched identity")
    methods = {item.id: item for item in descriptor.verification_methods}
    method = methods.get(record.proof.verification_method)
    if method is None or "record" not in method.purposes:
        raise ValueError("Record proof uses an unknown or unauthorized verification method")
    if not NodeKeyManager.verify(
        record.model_dump(mode="json"),
        record.proof.proof_value,
        method.public_key_base64,
    ):
        raise ValueError("Record proof verification failed")
    return record, endpoint, response.text