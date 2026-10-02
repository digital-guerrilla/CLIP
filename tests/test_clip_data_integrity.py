import unittest
from datetime import datetime, timezone

import nacl.signing

from node.app.core.data_integrity import (
    add_data_integrity_proof,
    base58btc_decode,
    base58btc_encode,
    verify_data_integrity_proof,
)
from node.app.core.ifc_protocol import IfcProposalTransaction


class DataIntegrityTest(unittest.TestCase):
    def test_eddsa_jcs_2022_matches_w3c_test_vector(self) -> None:
        document = {
            "@context": [
                "https://www.w3.org/ns/credentials/v2",
                "https://www.w3.org/ns/credentials/examples/v2",
            ],
            "id": "urn:uuid:58172aac-d8ba-11ed-83dd-0b3aef56cc33",
            "type": ["VerifiableCredential", "AlumniCredential"],
            "name": "Alumni Credential",
            "description": "A minimum viable example of an Alumni Credential.",
            "issuer": "https://vc.example/issuers/5678",
            "validFrom": "2023-01-01T00:00:00Z",
            "credentialSubject": {
                "id": "did:example:abcdefgh",
                "alumniOf": "The School of Examples",
            },
        }
        secret_key_multibase = "z3u2en7t5LR2WtQH5PfFqMqwVHBeXouLzo6haApm8XHqvjxq"
        secret_key = base58btc_decode(secret_key_multibase)
        self.assertEqual(secret_key[:2], bytes.fromhex("8026"))
        signing_key = nacl.signing.SigningKey(secret_key[2:])

        secured = add_data_integrity_proof(
            document,
            signing_key,
            verification_method=(
                "did:key:z6MkrJVnaZkeFzdQyMZu1cgjg7k1pZZ6pvBQ7XJPt4swbTQ2"
                "#z6MkrJVnaZkeFzdQyMZu1cgjg7k1pZZ6pvBQ7XJPt4swbTQ2"
            ),
            proof_purpose="assertionMethod",
            created=datetime(2023, 2, 24, 23, 36, 38, tzinfo=timezone.utc),
        )

        self.assertEqual(
            secured["proof"]["proofValue"],
            "z2HnFSSPPBzR36zdDgK8PbEHeXbR56YF24jwMpt3R1eHXQzJDMWS93FCzpvJpwTWd3GAVFuUfjoJdcnTMuVor51aX",
        )
        self.assertTrue(verify_data_integrity_proof(secured, signing_key.verify_key))

    def test_tampering_and_wrong_key_fail_verification(self) -> None:
        signing_key = nacl.signing.SigningKey(bytes(range(32)))
        secured = add_data_integrity_proof(
            {"transaction": "component.update", "sequence": 7},
            signing_key,
            verification_method="did:web:example.org#key-1",
            proof_purpose="capabilityInvocation",
            created=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        altered = {**secured, "sequence": 8}
        wrong_key = nacl.signing.SigningKey(bytes(reversed(range(32))))

        self.assertFalse(verify_data_integrity_proof(altered, signing_key.verify_key))
        self.assertFalse(verify_data_integrity_proof(secured, wrong_key.verify_key))

    def test_signed_component_proposal_parses_and_verifies(self) -> None:
        signing_key = nacl.signing.SigningKey(bytes(range(32)))
        proposal = IfcProposalTransaction.model_validate({
            "actorDid": "did:web:supplier.example",
            "target": {
                "authorityDid": "did:web:owner.example",
                "datasetId": "building.ifcx",
                "entityPath": "building/door-1",
                "componentSchemaId": "ifc::name",
            },
            "change": {"action": "set", "value": "Fire door"},
            "expectedSequence": 4,
            "schemaDigest": "a" * 64,
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
        unsigned = proposal.model_dump(mode="json", by_alias=True, exclude={"proof"})
        secured = add_data_integrity_proof(
            unsigned,
            signing_key,
            verification_method="did:web:supplier.example#key-1",
            proof_purpose="assertionMethod",
            created=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )

        parsed = IfcProposalTransaction.model_validate(secured)

        self.assertEqual(parsed.target.entity_path, "building/door-1")
        self.assertTrue(verify_data_integrity_proof(secured, signing_key.verify_key))

    def test_base58btc_round_trips_leading_zero_bytes(self) -> None:
        value = b"\0\0\x01\x02\xff"

        self.assertEqual(base58btc_decode(base58btc_encode(value)), value)


if __name__ == "__main__":
    unittest.main()