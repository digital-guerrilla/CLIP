import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from node.app.core.ifc_protocol import (
    IfcAuthorityReceipt,
    IfcDecisionTransaction,
    IfcProposalTransaction,
)
from node.app.core.ifc_graph import flatten_ifc_layers
from node.app.core.ifcx_models import IfcxFile
from node.app.db.ifc_store import (
    DuplicateIfcTransaction,
    IfcSequenceConflict,
    append_accepted_transaction,
    load_ifc_dataset,
    register_ifc_dataset,
    reject_ifc_proposal,
    store_ifc_proposal,
)
from node.app.db.orm_models import (
    Base,
    IfcAcceptedTransaction,
    IfcAuthoritySequence,
    IfcProposalRecord,
    IfcReceiptRecord,
)


class IfcxStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def create_receipt(self, transaction_id, *, accepted: bool = True):
        def factory(sequence: int, digest: str) -> IfcAuthorityReceipt:
            return IfcAuthorityReceipt(
                authorityDid="did:web:owner.example",
                transactionId=transaction_id,
                accepted=accepted,
                sequence=sequence,
                transactionDigest=digest,
                created=datetime.now(timezone.utc),
                proof={
                    "type": "DataIntegrityProof",
                    "cryptosuite": "eddsa-jcs-2022",
                    "created": "2026-10-01T00:00:00Z",
                    "verificationMethod": "did:web:owner.example#receipt-key",
                    "proofPurpose": "assertionMethod",
                    "proofValue": "z" + "1" * 86,
                },
            )

        return factory

    def create_proposal(self, schema_digest: str = "a" * 64) -> IfcProposalTransaction:
        return IfcProposalTransaction.model_validate({
            "actorDid": "did:web:supplier.example",
            "target": {
                "authorityDid": "did:web:owner.example",
                "datasetId": "building.ifcx",
                "entityPath": "building/door-1",
                "componentSchemaId": "ifc::name",
            },
            "change": {"action": "set", "value": "Fire door"},
            "expectedSequence": 0,
            "schemaDigest": schema_digest,
            "created": "2026-10-01T00:00:00Z",
            "proof": {
                "type": "DataIntegrityProof",
                "cryptosuite": "eddsa-jcs-2022",
                "created": "2026-10-01T00:00:00Z",
                "verificationMethod": "did:web:supplier.example#key-1",
                "proofPurpose": "assertionMethod",
                "proofValue": "z" + "1" * 86,
            },
        })

    def create_decision(
        self,
        proposal: IfcProposalTransaction,
        proposal_digest: str,
        decision: str,
        expected_sequence: int,
    ) -> IfcDecisionTransaction:
        return IfcDecisionTransaction.model_validate({
            "actorDid": "did:web:owner.example",
            "decision": decision,
            "proposalId": str(proposal.transaction_id),
            "proposalDigest": proposal_digest,
            "expectedSequence": expected_sequence,
            "created": "2026-10-01T00:00:01Z",
            "proof": {
                "type": "DataIntegrityProof",
                "cryptosuite": "eddsa-jcs-2022",
                "created": "2026-10-01T00:00:01Z",
                "verificationMethod": "did:web:owner.example#key-1",
                "proofPurpose": "capabilityInvocation",
                "proofValue": "z" + "1" * 86,
            },
        })

    async def test_transactions_sequence_per_authority_and_store_receipt_atomically(self) -> None:
        first_id = uuid4()
        second_id = uuid4()

        async with self.sessions() as session:
            first_receipt = await append_accepted_transaction(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
                expected_sequence=0,
                transaction={
                    "transactionId": str(first_id),
                    "actorDid": "did:web:supplier.example",
                    "expectedSequence": 0,
                },
                create_receipt=self.create_receipt(first_id),
            )
            second_receipt = await append_accepted_transaction(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
                expected_sequence=1,
                transaction={
                    "transactionId": str(second_id),
                    "actorDid": "did:web:owner.example",
                    "expectedSequence": 1,
                },
                create_receipt=self.create_receipt(second_id),
            )

            self.assertEqual((first_receipt.sequence, second_receipt.sequence), (1, 2))
            self.assertEqual(
                (await session.execute(select(IfcAuthoritySequence))).scalar_one().sequence,
                2,
            )
            self.assertEqual(len((await session.execute(select(IfcAcceptedTransaction))).scalars().all()), 2)
            self.assertEqual(len((await session.execute(select(IfcReceiptRecord))).scalars().all()), 2)

    async def test_stale_sequence_and_duplicate_transaction_are_rejected(self) -> None:
        transaction_id = uuid4()
        transaction = {
            "transactionId": str(transaction_id),
            "actorDid": "did:web:supplier.example",
            "expectedSequence": 0,
        }

        async with self.sessions() as session:
            await append_accepted_transaction(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
                expected_sequence=0,
                transaction=transaction,
                create_receipt=self.create_receipt(transaction_id),
            )
            with self.assertRaises(IfcSequenceConflict):
                await append_accepted_transaction(
                    session,
                    authority_did="did:web:owner.example",
                    dataset_id="building.ifcx",
                    expected_sequence=0,
                    transaction={
                        "transactionId": str(uuid4()),
                        "actorDid": "did:web:supplier.example",
                        "expectedSequence": 0,
                    },
                    create_receipt=self.create_receipt(uuid4()),
                )

            with self.assertRaises(DuplicateIfcTransaction):
                await append_accepted_transaction(
                    session,
                    authority_did="did:web:owner.example",
                    dataset_id="building.ifcx",
                    expected_sequence=1,
                    transaction=transaction,
                    create_receipt=self.create_receipt(transaction_id),
                )

            with self.assertRaisesRegex(ValueError, "expectedSequence"):
                await append_accepted_transaction(
                    session,
                    authority_did="did:web:owner.example",
                    dataset_id="building.ifcx",
                    expected_sequence=1,
                    transaction={
                        "transactionId": str(uuid4()),
                        "actorDid": "did:web:supplier.example",
                        "expectedSequence": 0,
                    },
                    create_receipt=self.create_receipt(uuid4()),
                )

    async def test_owner_acceptance_commits_proposal_decision_and_receipt(self) -> None:
        async with self.sessions() as session:
            dataset = IfcxFile.model_validate({
                "header": {
                    "id": "building.ifcx",
                    "ifcxVersion": "ifcx_alpha",
                    "dataVersion": "1.0.0",
                    "author": "owner",
                    "timestamp": "2026-10-01T00:00:00Z",
                },
                "imports": [],
                "schemas": {"ifc::name": {"value": {"dataType": "String"}}},
                "data": [{
                    "path": "building/door-1",
                    "attributes": {"ifc::name": "Door"},
                }],
            })
            schema_digest = await register_ifc_dataset(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
                file=dataset,
            )
            proposal = self.create_proposal(schema_digest)
            proposal_digest = await store_ifc_proposal(session, proposal)
            decision = self.create_decision(proposal, proposal_digest, "accept", 0)
            receipt = await append_accepted_transaction(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
                expected_sequence=0,
                transaction=decision.model_dump(mode="json", by_alias=True),
                create_receipt=self.create_receipt(decision.transaction_id),
                proposal_id=proposal.transaction_id,
            )
            stored_proposal = await session.get(
                IfcProposalRecord,
                str(proposal.transaction_id),
            )
            stored_transaction = await session.get(
                IfcAcceptedTransaction,
                str(decision.transaction_id),
            )
            stored_receipt = await session.get(IfcReceiptRecord, str(receipt.receipt_id))
            updated_dataset = await load_ifc_dataset(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
            )
            updated_graph = flatten_ifc_layers(
                [updated_dataset],
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
            )

        self.assertIsNotNone(stored_proposal)
        self.assertEqual(stored_proposal.status, "accepted")
        self.assertEqual(stored_transaction.sequence, 1)
        self.assertEqual(
            stored_transaction.transaction_json["proposal"]["transactionId"],
            str(proposal.transaction_id),
        )
        self.assertEqual(stored_receipt.receipt_json["transactionDigest"], receipt.transaction_digest)
        self.assertEqual(
            updated_graph.entities["building/door-1"].components["ifc::name"],
            "Fire door",
        )
        self.assertEqual(len(updated_dataset.data), len(dataset.data) + 1)

    async def test_rejection_is_recorded_without_advancing_authority_sequence(self) -> None:
        proposal = self.create_proposal()

        async with self.sessions() as session:
            proposal_digest = await store_ifc_proposal(session, proposal)
            decision = self.create_decision(proposal, proposal_digest, "reject", 0)
            receipt = await reject_ifc_proposal(
                session,
                authority_did="did:web:owner.example",
                dataset_id="building.ifcx",
                proposal_id=proposal.transaction_id,
                decision=decision.model_dump(mode="json", by_alias=True),
                create_receipt=self.create_receipt(decision.transaction_id, accepted=False),
            )
            stored_proposal = await session.get(
                IfcProposalRecord,
                str(proposal.transaction_id),
            )
            sequence = await session.get(IfcAuthoritySequence, "did:web:owner.example")

        self.assertEqual(stored_proposal.status, "rejected")
        self.assertFalse(receipt.accepted)
        self.assertEqual(receipt.sequence, 0)
        self.assertEqual(
            stored_proposal.decision_receipt_json["transactionId"],
            str(decision.transaction_id),
        )
        self.assertIsNone(sequence)


if __name__ == "__main__":
    unittest.main()