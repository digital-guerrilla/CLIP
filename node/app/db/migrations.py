"""Forward-only CLIP infrastructure and IFC graph schema migrations."""

import hashlib
import json

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, UniqueConstraint, inspect, select, LargeBinary

metadata = MetaData()
ledger = Table("clip_schema_revisions", metadata,
    Column("revision", Integer, primary_key=True), Column("checksum", String(64), nullable=False))


def field(name, kind=String, *, primary=False, nullable=False, foreign=None):
    arguments = [ForeignKey(foreign)] if foreign else []
    return Column(name, kind, *arguments, primary_key=primary, nullable=nullable)


def table(name, *columns, unique=()):
    constraints = [UniqueConstraint(*names) for names in unique]
    return Table(name, metadata, *columns, *constraints)


REVISION_1 = (
    table("ifcx_authority_sequences", field("authority_did", primary=True), field("sequence", Integer)),
    table("ifcx_proposals", field("proposal_id", primary=True), field("authority_did"), field("dataset_id"),
        field("proposer_did"), field("proposal_digest", String(64)), field("proposal_json", JSON),
        field("status", String(16)), field("decision_json", JSON, nullable=True), field("decision_receipt_json", JSON, nullable=True),
        field("created_at", DateTime(timezone=True)), field("updated_at", DateTime(timezone=True))),
    table("ifcx_datasets", field("authority_did", primary=True), field("dataset_id", primary=True),
        field("schema_digest", String(64)), field("file_json", JSON), field("updated_at", DateTime(timezone=True))),
    table("ifcx_dataset_trust_policies", field("authority_did", primary=True), field("dataset_id", primary=True),
        field("trusted_proposers", JSON), field("updated_at", DateTime(timezone=True))),
    table("ifcx_peers", field("peer_did", primary=True), field("status", String(16)), field("generation", Integer),
        field("last_seen", DateTime(timezone=True), nullable=True), field("discovered_from", nullable=True)),
    table("ifcx_gossip_generations", field("authority_did", primary=True), field("generation", Integer)),
    table("ifcx_accepted_transactions", field("transaction_id", primary=True), field("authority_did"), field("dataset_id"),
        field("sequence", Integer), field("transaction_digest", String(64)), field("transaction_json", JSON),
        field("created_at", DateTime(timezone=True)), unique=(("authority_did", "sequence"),)),
    table("ifcx_receipts", field("receipt_id", primary=True), field("transaction_id", foreign="ifcx_accepted_transactions.transaction_id"),
        field("authority_did"), field("sequence", Integer), field("transaction_digest", String(64)), field("receipt_json", JSON),
        field("created_at", DateTime(timezone=True)), unique=(("authority_did", "sequence"), ("transaction_id",))),
)

REVISION_2 = (
    table("ifcx_replica_bundles", field("authority_did", primary=True), field("dataset_id", primary=True),
        field("sequence", Integer), field("digest", String(64)), field("bundle_json", JSON), field("received_at", DateTime(timezone=True))),
    table("ifcx_replication_acks", field("digest", String(64), primary=True), field("peer_did", primary=True),
        field("acknowledgement_json", JSON), field("received_at", DateTime(timezone=True))),
    table("ifcx_evidence", field("evidence_id", String(64), primary=True), field("publisher_did"),
        field("manifest_json", JSON), field("expires_at", DateTime(timezone=True), nullable=True)),
    table("ifcx_fragment_receipts", field("evidence_id", String(64), primary=True), field("fragment_index", Integer, primary=True),
        field("peer_did", primary=True), field("receipt_json", JSON)),
)
REVISION_3 = (table("ifcx_import_cache", field("uri", primary=True), field("integrity", primary=True), field("content", LargeBinary), field("fetched_at", DateTime(timezone=True))),)
REVISION_4 = (
    table("ifcx_project_policies", field("authority_did", primary=True), field("dataset_id", primary=True),
        field("name"), field("visibility", String(16)), field("members", JSON), field("revision", Integer),
        field("updated_at", DateTime(timezone=True))),
)
REVISION_5 = (
    table("ifcx_project_invites", field("invite_id", primary=True), field("authority_did"), field("dataset_id"),
        field("token_digest", String(64)), field("role", String(16)), field("status", String(16)),
        field("expires_at", DateTime(timezone=True)), field("created_at", DateTime(timezone=True)),
        unique=(("token_digest",),)),
    table("ifcx_project_join_requests", field("request_id", primary=True),
        field("invite_id", foreign="ifcx_project_invites.invite_id"), field("authority_did"), field("dataset_id"),
        field("actor_did"), field("role", String(16)), field("status", String(16)),
        field("request_json", JSON), field("decision_json", JSON, nullable=True),
        field("created_at", DateTime(timezone=True)), field("updated_at", DateTime(timezone=True)),
        unique=(("invite_id",),)),
)
REVISION_6 = (
    table("ifcx_supply_chain_records", field("record_id", primary=True), field("authority_did"), field("kind"),
        field("project_id", nullable=True), field("revision", Integer), field("record_json", JSON)),
    table("ifcx_supply_chain_revisions", field("authority_did", primary=True), field("record_id", primary=True),
        field("revision", Integer, primary=True), field("public", Boolean), field("snapshot_json", JSON)),
    table("ifcx_supply_chain_documents", field("document_id", primary=True), field("record_id"),
        field("metadata_json", JSON), field("ciphertext", LargeBinary)),
    table("ifcx_supply_chain_submissions", field("submission_id", primary=True), field("direction"),
        field("revision", Integer), field("submission_json", JSON)),
    table("ifcx_supply_chain_operations", field("operation_key", primary=True), field("request_digest"),
        field("response_json", JSON)),
    table("ifcx_supply_chain_projects", field("authority_did", primary=True), field("project_id", primary=True),
        field("project_json", JSON)),
)
REVISION_7 = (table("ifcx_supply_chain_document_grants", field("document_id", primary=True),
    field("recipient_did", primary=True), field("grant_json", JSON)),)
REVISION_8 = (table("ifcx_supply_chain_sender_policies", field("project_id", primary=True),
    field("revision", Integer), field("senders", JSON)),)
NAMESPACE_TABLES = {
    "ifcx_authority_sequences": "ifc_authority_sequences",
    "ifcx_proposals": "ifc_proposals",
    "ifcx_datasets": "ifc_datasets",
    "ifcx_dataset_trust_policies": "ifc_dataset_trust_policies",
    "ifcx_accepted_transactions": "ifc_accepted_transactions",
    "ifcx_receipts": "ifc_receipts",
    "ifcx_peers": "clip_peers",
    "ifcx_gossip_generations": "clip_gossip_generations",
    "ifcx_replica_bundles": "clip_replica_bundles",
    "ifcx_replication_acks": "clip_replication_acks",
    "ifcx_evidence": "clip_evidence",
    "ifcx_fragment_receipts": "clip_fragment_receipts",
    "ifcx_import_cache": "clip_import_cache",
    "ifcx_project_policies": "clip_project_policies",
    "ifcx_project_invites": "clip_project_invites",
    "ifcx_project_join_requests": "clip_project_join_requests",
    "ifcx_supply_chain_records": "clip_supply_chain_records",
    "ifcx_supply_chain_revisions": "clip_supply_chain_revisions",
    "ifcx_supply_chain_documents": "clip_supply_chain_documents",
    "ifcx_supply_chain_document_grants": "clip_supply_chain_document_grants",
    "ifcx_supply_chain_submissions": "clip_supply_chain_submissions",
    "ifcx_supply_chain_operations": "clip_supply_chain_operations",
    "ifcx_supply_chain_projects": "clip_supply_chain_projects",
    "ifcx_supply_chain_sender_policies": "clip_supply_chain_sender_policies",
}
REVISION_9 = ()
REVISIONS = (REVISION_1, REVISION_2, REVISION_3, REVISION_4, REVISION_5, REVISION_6, REVISION_7, REVISION_8, REVISION_9)


def checksum(tables) -> str:
    specification = [{"name": item.name,
        "columns": [(column.name, str(column.type), column.nullable, column.primary_key) for column in item.columns],
        "unique": sorted(tuple(column.name for column in constraint.columns) for constraint in item.constraints if isinstance(constraint, UniqueConstraint)),
        "foreign": sorted((foreign.parent.name, foreign.target_fullname) for foreign in item.foreign_keys),
    } for item in tables]
    return hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()


def upgrade(connection) -> None:
    existing_tables = set(inspect(connection).get_table_names())
    if "ifcx_schema_revisions" in existing_tables:
        if ledger.name in existing_tables:
            raise RuntimeError("Both legacy and CLIP migration ledgers exist; explicit recovery required")
        connection.exec_driver_sql('ALTER TABLE "ifcx_schema_revisions" RENAME TO "clip_schema_revisions"')
    ledger.create(connection, checkfirst=True)
    applied = dict(connection.execute(select(ledger.c.revision, ledger.c.checksum)).all())
    if set(applied) - set(range(1, len(REVISIONS) + 1)):
        raise RuntimeError("Database schema is newer than this application; downgrade refused")
    if sorted(applied) != list(range(1, len(applied) + 1)):
        raise RuntimeError("Database migration history is not contiguous")
    for revision, tables in enumerate(REVISIONS, start=1):
        expected = (hashlib.sha256(json.dumps(NAMESPACE_TABLES, sort_keys=True).encode()).hexdigest()
            if revision == 9 else checksum(tables))
        if revision in applied and applied[revision] != expected:
            raise RuntimeError(f"Migration {revision} checksum changed; restore the original revision")
        if revision == 9:
            if revision not in applied:
                existing_tables = set(inspect(connection).get_table_names())
                for old_name, new_name in NAMESPACE_TABLES.items():
                    if new_name in existing_tables or old_name not in existing_tables:
                        raise RuntimeError(f"Cannot rename {old_name} to {new_name}; explicit recovery required")
                    connection.exec_driver_sql(f'ALTER TABLE "{old_name}" RENAME TO "{new_name}"')
                connection.execute(ledger.insert().values(revision=revision, checksum=expected))
            continue
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for item in tables:
            table_name = NAMESPACE_TABLES[item.name] if 9 in applied else item.name
            if table_name in existing_tables:
                existing = inspector.get_columns(table_name)
                expected_names = {column.name for column in item.columns}
                if {column["name"] for column in existing} != expected_names:
                    raise RuntimeError(f"Incompatible schema for {item.name}; explicit migration required")
                for actual in existing:
                    expected_column = item.c[actual["name"]]
                    if actual["type"]._type_affinity != expected_column.type._type_affinity or actual["nullable"] != expected_column.nullable:
                        raise RuntimeError(f"Incompatible column {item.name}.{actual['name']}")
                unique_sets = {frozenset(constraint["column_names"]) for constraint in inspector.get_unique_constraints(table_name)}
                for constraint in item.constraints:
                    if isinstance(constraint, UniqueConstraint) and frozenset(column.name for column in constraint.columns) not in unique_sets:
                        raise RuntimeError(f"Missing unique constraint on {item.name}")
                primary = set(inspector.get_pk_constraint(table_name)["constrained_columns"])
                if primary != {column.name for column in item.primary_key.columns}:
                    raise RuntimeError(f"Incompatible primary key for {item.name}")
            elif revision in applied:
                raise RuntimeError(f"Applied migration table {item.name} is missing; restore from backup")
            else:
                item.create(connection)
        if revision not in applied:
            connection.execute(ledger.insert().values(revision=revision, checksum=expected))