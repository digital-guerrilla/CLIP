"""
Node configuration loaded from environment variables (or a .env file).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ------------------------------------------------------------------
    # Node Identity
    # ------------------------------------------------------------------
    # The domain (and optional port) that identifies this node in CLIP URIs.
    # For production: "products.acme.com"
    # For development: "localhost:8000"
    NODE_DOMAIN: str = "localhost:8000"

    # The externally reachable base URL of this node's API.
    # Advertised in the .well-known/clip/server discovery document.
    NODE_API_BASE: str = "http://localhost:8000"

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    DATABASE_URL: str = "sqlite+aiosqlite:///./clip_node.db"

    # Path to the persisted Ed25519 private key (32 bytes, binary).
    PRIVATE_KEY_FILE: str = "./node_private_key.bin"

    # Local content-addressed storage for uploaded and replicated documents.
    DOCUMENT_STORAGE_DIR: str = "./data/documents"

    # Maximum accepted document size in bytes (25 MiB by default).
    MAX_DOCUMENT_BYTES: int = 25 * 1024 * 1024

    # Opt-in encrypted fragment storage. Disabled unless explicitly enabled.
    ENCRYPTED_STORAGE_OPT_IN: bool = False
    ENCRYPTED_STORAGE_CAPACITY_BYTES: int = 0
    ENCRYPTED_STORAGE_DEFAULT_RETENTION_SECONDS: int = 0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    # Static API key for write operations (POST/PUT).
    # Replace with JWT or mTLS in production.
    API_KEY: str = "change-me-before-deployment"

    # ------------------------------------------------------------------
    # Federation
    # ------------------------------------------------------------------
    # Timeout in seconds for outbound federation HTTP calls.
    FEDERATION_TIMEOUT: int = 10

    # Permit HTTP discovery for non-loopback routing hosts in development networks.
    ALLOW_INSECURE_HTTP_DISCOVERY: bool = False

    # How long (seconds) to honour a cached remote record before considering
    # it stale. Set to 0 to always re-fetch from the authority.
    CACHE_TTL: int = 3600

    # ------------------------------------------------------------------
    # Gossip
    # ------------------------------------------------------------------
    # Comma-separated list of seed peer base URLs for bootstrap.
    # e.g. "http://node2.local:8001,http://node3.local:8002"
    GOSSIP_SEEDS: str = ""

    # How often (seconds) to run a gossip round (pick a random peer and exchange digests)
    GOSSIP_INTERVAL: int = 15

    # Seconds without a heartbeat before marking a peer as 'suspect'
    GOSSIP_SUSPECT_TIMEOUT: int = 45

    # Seconds after being suspected before marking as 'dead'
    GOSSIP_DEAD_TIMEOUT: int = 120

    # Node role for startup behavior and capability defaults.
    # Supported values:
    # - producer
    # - resolver
    # - manufacturer
    # - supplier
    # - contractor
    # - client
    # - validator
    # - relay
    NODE_ROLE: str = "resolver"

    # ------------------------------------------------------------------
    # DID (did:web)
    # ------------------------------------------------------------------
    DID_WEB_ID: str = ""


settings = Settings()
