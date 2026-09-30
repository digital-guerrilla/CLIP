# CLIP: A Trusted History for Every Asset

## Elevator Pitch

Every important physical asset has a story. A piece of equipment was made by
one company, delivered by another, installed by a contractor, checked by an
inspector, and eventually maintained by an owner.

Today, that story is usually scattered across emails, spreadsheets, company
systems, and paper documents. CLIP connects the story without forcing all of
those companies to put their information into one central database.

Each organisation keeps control of the information it created. CLIP links the
relevant pieces together, records who supplied each piece of information, and
lets an authorised person check whether it can be trusted.

In one sentence: **CLIP creates a trusted, shared history of an asset while
allowing each organisation to keep control of its own information.**

## 20-Minute Presentation

### 1. The everyday problem (2 minutes)

Imagine a large hospital with an air-handling unit in one of its plant rooms.

Several organisations have important information about it:

- The manufacturer knows what the equipment was designed to do.
- The supplier knows when it was delivered.
- The contractor knows which project bought and installed it.
- The inspector knows whether it passed its checks.
- The hospital knows where it is and whether it is currently in service.

That information is often kept in separate systems. When someone needs to
answer a simple question, such as “Can we prove this unit was installed and
inspected properly?”, they may have to search through several systems and ask
several companies for documents.

The problem is not just finding the information. It is knowing whether the
information is genuine, current, complete, and provided by the right person.

### 2. The simple idea (2 minutes)

CLIP creates a connected history for the asset.

Think of it as a set of labelled links:

```text
The hospital's equipment record
  |-- links to the manufacturer's product information
  |-- links to the supplier's delivery information
  |-- links to the contractor's project information
  `-- links to the inspector's test result
```

The hospital's record is the starting point because the hospital owns or
manages the physical equipment. But the hospital does not rewrite everybody
else's information. It keeps links to the original sources.

This is similar to having a trusted contents page for an asset's history. The
contents page tells you where each fact came from, while the original author
still owns the fact.

### 3. Why not put everything in one database? (2 minutes)

A single database sounds convenient, but it creates new problems:

- One organisation becomes responsible for everybody else's information.
- A change made by one party may accidentally overwrite another party's fact.
- Companies may not want to share confidential commercial information.
- If the central system goes down, everyone may lose access.
- People may disagree about which copy is the official one.

CLIP lets organisations cooperate without giving up control. Each party shares
only the information needed for the asset's history.

For example, a supplier can prove that a piece of equipment was delivered
without sharing its invoices, prices, or wider commercial records.

### 4. How CLIP knows who said what (1 minute)

Every piece of information carries a tamper-evident seal from the organisation
that published it.

That seal helps answer three questions:

1. Which organisation published this information?
2. Has the information been changed since it was published?
3. Does the organisation still stand behind this version?

The web address used to find the information is not enough on its own. A web
address can change or be taken over. CLIP gives each organisation a lasting
identity that is tied to its own signing key.

In everyday terms, the address tells us where to look; the organisation's
identity tells us why we should believe what we find there.

### 5. The four kinds of information (1 minute)

CLIP keeps the information simple and recognisable:

- **Product information:** what the manufacturer says about a type of product.
- **Asset information:** the identity and location of one real physical item.
- **Event or evidence information:** something that happened, such as a
  delivery, installation, repair, or inspection.
- **Group information:** a collection of items, such as a building system or
  equipment package.

The asset record contains a useful summary, but it does not need to contain
every document ever created about the asset. The detailed information can stay
with the organisation that created it.

### 6. How organisations agree to a link (3 minutes)

A link in the asset history is not added silently.

Suppose an inspector wants to add a commissioning result to the hospital's
asset history:

1. The inspector publishes the result and stands behind it.
2. The inspector asks the hospital to connect that result to the equipment.
3. The hospital checks the result and decides whether to accept the link.
4. The hospital records that acceptance in its own asset history.

This creates a clear division of responsibility. The inspector is responsible
for the inspection result. The hospital is responsible for deciding that the
result belongs in its asset history.

The same process can be used for delivery, procurement, installation,
maintenance, and ownership changes.

### 7. What happens when someone looks up an asset? (2 minutes)

An authorised user starts with the asset record and asks CLIP to show its
connected history.

CLIP then:

- checks each piece of information before trusting it;
- follows the links to the manufacturer, supplier, contractor, and inspector;
- stops the search from growing without limit;
- hides information the user is not allowed to see; and
- clearly reports information that is missing, unavailable, or out of date.

This last point matters. CLIP does not pretend that missing information is good
news. It shows where the history is complete and where further investigation is
needed.

### 8. Live example: one asset, six services (3 minutes)

The demonstration uses six small services to represent the organisations and
the independent checking service:

| Service | Represents | Provides | Record or job | Access |
|---|---|---|---|---|
| 8101 | Manufacturer | Product information | Product record | Public |
| 8102 | Supplier | Delivery information | Delivery record | Approved users |
| 8103 | Main contractor | Project and procurement information | Project record | Approved users |
| 8104 | Owner | The hospital's asset record | Asset record | Owner controlled |
| 8105 | Inspector | Commissioning result | Inspection record | Approved users |
| 8106 | Relay | Finds and checks the history | Checked view | Granted access |

Suggested narration:

1. Create the five records separately.
2. Ask each organisation to connect its record to the hospital's equipment.
3. Have the hospital accept each connection.
4. Ask the separate relay service to build the complete history.
5. Show that the result contains five checked records and four accepted links.
6. Show that a contractor cannot see the hospital's private information unless
   the hospital has granted access.

The relay is a helper, not the owner of the information. It can find and check
the records, but it does not become the source of truth for any organisation.

### 9. What CLIP does when things go wrong (2 minutes)

Real projects are messy. A company may be offline, a document may be private,
or an old record may be replaced by a newer one.

CLIP handles this openly:

- A checked copy can be used temporarily if the original organisation is
  unavailable.
- That copy is marked as older, so people know it may need checking later.
- Private information is shown only to approved users and services.
- Old versions are kept so the history cannot simply be rewritten.
- Missing links are reported instead of being silently ignored.

The goal is not to claim that every asset history is always perfect. The goal
is to make the trustworthy parts clear and the gaps visible.

### 10. What CLIP is and is not (2 minutes)

CLIP is a way for existing business systems to publish and connect trusted
asset information.

It is not intended to replace:

- an enterprise resource planning system;
- a supplier or logistics system;
- a contractor's project system;
- a facilities-management system; or
- a document-management system.

Those systems can remain in place. CLIP provides a shared, checkable view of
the important facts that need to travel between organisations.

The current project is a working demonstration and research implementation.
Before production use, it needs additional security, operational controls,
stronger access management, backup and recovery work, and testing with other
independent implementations.

## Closing Message

When an owner asks, “What do we know about this asset, where did that
information come from, and can we trust it?”, CLIP provides a structured
answer.

It connects the asset's story without forcing every organisation to hand over
its whole filing cabinet. Each organisation remains responsible for its own
facts, the owner decides what belongs in the asset history, and authorised
people can see both the evidence and any gaps.

**One asset. Many organisations. One history that can be checked.**

## Demo Preparation

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\examples\run-network.ps1
.\examples\seed-network.ps1
.\examples\verify-network.ps1
```

The demonstration credentials are for demo use only and should not be treated
as a production security model.