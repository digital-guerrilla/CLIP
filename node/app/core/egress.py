"""DNS-pinned outbound HTTP with public-address and operator allowlist checks."""

import asyncio
import ipaddress
import json
import socket

import httpcore
from httpcore._backends.auto import AutoBackend
import httpx

from ..config import settings


class EgressError(ValueError):
    pass


async def allowed_addresses(host: str, port: int) -> list[str]:
    allowed_hosts = {value.strip().casefold() for value in settings.CLIP_EGRESS_ALLOWED_HOSTS.split(",") if value.strip()}
    if allowed_hosts and host.casefold() not in allowed_hosts:
        raise EgressError("Outbound hostname is not in the operator allowlist")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list({ipaddress.ip_address(answer[4][0]) for answer in answers})
    loopback = settings.CLIP_ALLOW_HTTP_LOOPBACK and host.casefold() in {"localhost", "127.0.0.1", "::1"}
    if not addresses or any(not address.is_global and not (loopback and address.is_loopback) for address in addresses):
        raise EgressError("Outbound DNS must resolve exclusively to public addresses")
    if port != 443 and not loopback:
        raise EgressError("Production outbound services must use port 443")
    return [str(address) for address in addresses]


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.backend = AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        hostname = host.decode() if isinstance(host, bytes) else host
        addresses = await allowed_addresses(hostname, port)
        return await self.backend.connect_tcp(addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise EgressError("Unix socket egress is forbidden")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


def public_http_client(timeout: float = 10) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=16, max_keepalive_connections=8), retries=0)
    transport._pool._network_backend = PublicNetworkBackend()
    return httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False)


def decode_json(content: bytes) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON member")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number {value} is forbidden")

    return json.loads(content, object_pairs_hook=unique_pairs, parse_constant=reject_constant)


async def request_json(method: str, url: str, *, body: dict | None = None) -> dict:
    async with public_http_client(settings.FEDERATION_TIMEOUT) as client:
        async with client.stream(method, url, json=body) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > settings.CLIP_MAX_SERVICE_BYTES:
                    raise EgressError("Service response exceeds the configured byte limit")
    return decode_json(bytes(content))