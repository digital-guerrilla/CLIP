"""CLIP v3 URI parsing and generation."""

import re
import uuid
from dataclasses import dataclass

CLIP_SCHEME = "clip"

_CLIP_RE = re.compile(
    r"^clip://"
    r"([a-zA-Z0-9.-]+(?::\d+)?)"
    r"/(z[1-9A-HJ-NP-Za-km-z]+)"
    r"/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedCLIP:
    routing_host: str
    authority_key_fingerprint: str
    record_uuid: str

    @property
    def full_id(self) -> str:
        return (
            f"clip://{self.routing_host}/{self.authority_key_fingerprint}/"
            f"{self.record_uuid}"
        )

    def __str__(self) -> str:
        return self.full_id


def generate_clip(routing_host: str, authority_key_fingerprint: str) -> str:
    """Generate a self-certifying CLIP v3 URI."""
    candidate = f"clip://{routing_host}/{authority_key_fingerprint}/{uuid.uuid4()}"
    return parse_clip(candidate).full_id


def parse_clip(clip: str) -> ParsedCLIP:
    """
    Parse a CLIP URI string into its components.

    Raises:
        ValueError: If the string is not a valid CLIP URI.
    """
    match = _CLIP_RE.match(clip.strip())
    if not match:
        raise ValueError(
            f"Invalid CLIP URI: {clip!r}. "
            "Expected format: clip://{routing-host}/{authority-key-fingerprint}/{uuid4}"
        )
    return ParsedCLIP(
        routing_host=match.group(1).lower(),
        authority_key_fingerprint=match.group(2),
        record_uuid=match.group(3).lower(),
    )


def is_valid_clip(clip: str) -> bool:
    """Return True if the string is a syntactically valid CLIP URI."""
    return bool(_CLIP_RE.match(clip.strip()))
