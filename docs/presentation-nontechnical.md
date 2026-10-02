# CLIP: Construction Facts With Accountability

The manufacturer describes a product. The contractor records an installation.
The owner decides whether to accept that installation. CLIP preserves who made
each claim and who accepted it, while keeping product information connected to
the installed asset.

Construction information uses IFCX, an emerging buildingSMART format for linked
building data. Organizations keep their own identities and sign their own facts.
One participant cannot silently make an owner decision for another.

Supporting documents can be encrypted, distributed to storage peers and delivered
only with keys sealed for intended recipients. Storage receipts and signed
manifests make missing or altered document fragments detectable. Retention dates
limit how long storage nodes should keep the fragments.

The demonstration links a manufacturer door type to an owner asset, records a
contractor installation and preserves the owner's acceptance. Automated tests
also check restart, temporary outages, replication and document repair.

CLIP is currently a research implementation. It does not guarantee every claim
is factually true, that every replica stays online, or that a recipient cannot
retain a document after decrypting it. Production deployment requires further
security, operational and interoperability qualification.