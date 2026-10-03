const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const script = readFileSync(join(__dirname, "../node/app/static/dashboard.js"), "utf8");
const html = readFileSync(join(__dirname, "../node/app/static/dashboard.html"), "utf8");
const supplyScript = script.slice(script.indexOf("// The operator key"));

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.childNodes = [];
    this.parentElement = null;
    this.attributes = {};
    this.dataset = {};
    this._value = "";
    this._text = "";
    this.hidden = false;
    this.open = false;
    this.checked = false;
    this.disabled = false;
    this.listeners = new Map();
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  set textContent(value) { this._text = String(value ?? ""); this.childNodes = []; }
  get textContent() { return this._text + this.childNodes.map((item) => item.textContent).join(" "); }
  set value(value) { this._value = String(value ?? ""); }
  get value() { return this._value || (this.tagName === "select" ? this.options[0]?.value || "" : ""); }
  get children() { return this.childNodes; }
  get options() { return this.childNodes.filter((item) => item.tagName === "option"); }
  get childElementCount() { return this.childNodes.length; }
  get lastChild() { return this.childNodes.at(-1); }
  append(...nodes) {
    for (const item of nodes) {
      item.remove();
      item.parentElement = this;
      this.childNodes.push(item);
    }
  }
  before(item) { this.parentElement?.append(item); }
  replaceChildren(...nodes) {
    for (const child of this.childNodes) child.parentElement = null;
    this.childNodes = [];
    this._text = "";
    this.append(...nodes);
  }
  remove() {
    if (this.parentElement) this.parentElement.childNodes = this.parentElement.childNodes.filter((item) => item !== this);
    this.parentElement = null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  querySelector(selector) {
    for (const item of this.childNodes) {
      if (selector.startsWith(".") ? item.className === selector.slice(1) : item.tagName === selector) return item;
      const nested = item.querySelector(selector);
      if (nested) return nested;
    }
    return null;
  }
  reset() {}
  reportValidity() { return true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  dispatchEvent(event) { this["on" + event.type]?.(event); }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
}

function environment(apiOverride) {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) {
      const element = new Element(/-(project|direction|filter|kind)$/.test(id) ? "select" : "div");
      element.id = id;
      elements.set(id, element);
    }
    return elements.get(id);
  };
  const node = (tag, text, className) => {
    const element = new Element(tag);
    if (text !== undefined) element.textContent = text;
    element.className = className;
    return element;
  };
  const nav = new Element("nav");
  for (const name of ["products", "supplied", "discover", "projects", "installations", "submissions", "documents", "assets", "overview", "activity", "replication"]) {
    const tab = get("tab-" + name); tab.append(node("span", name)); nav.append(tab);
    const section = get(name); section.append(node("div", undefined, "page-heading"));
  }
  get("catalogue-list").parentElement = get("discover");
  get("new-project").parentElement = new Element();
  get("key").value = "local-key";
  get("submission-direction").value = "incoming";
  const calls = [];
  let uuid = 0;
  const state = { info: { did: "did:web:local.example", role: "contractor" }, request: 1, authorized: true };
  const listeners = new Map();
  const document = { getElementById: get, createElement: (tag) => node(tag), body: new Element("body") };
  const context = vm.createContext({
    document, state, $: get, node, icons() {}, readable: (value) => String(value || ""),
    notice(message, error) { get("notice").textContent = message; get("notice").error = error; },
    api: async (path, method = "GET", body) => {
      calls.push({ path, method, body: body && JSON.parse(JSON.stringify(body)) });
      if (path.endsWith("/schema")) return { kinds: [{ kind: "product" }], typeClasses: ["IfcDoorType", "IfcPumpType"], occurrenceClasses: ["IfcDoor", "IfcPump"] };
      if (apiOverride) return apiOverride(path, method, body);
      return { items: [] };
    },
    window: { addEventListener(name, listener) { listeners.set(name, listener); } },
    crypto: { randomUUID: () => "test-key-" + ++uuid },
    setTimeout() {}, URLSearchParams,
    FileReader: class {
      readAsDataURL() { this.result = "data:application/pdf;base64,dGVzdA=="; this.onload(); }
    },
    Event: class { constructor(type) { this.type = type; } }, console,
  });
  vm.runInContext(script.slice(script.indexOf("async function mapConcurrent"), script.indexOf("async function refresh(")), context);
  vm.runInContext(supplyScript, context);
  return { ui: context.window.clipSupplyChain, get, calls, state, listeners };
}

const source = {
  id: "supply-1", authorityDid: "did:web:local.example", kind: "supply",
  name: "Accepted doors", revision: 1, status: "issued", projectId: "project-1",
  ifcClass: "IfcDoorType", data: { quantity: 3, unit: "each", serials: ["A", "B", "C"] },
  sources: [], acceptedFrom: { snapshot: { authorityDid: "did:web:supplier.example" }, issue: { submissionId: "issue-1" } },
};
const normalized = (value) => JSON.parse(JSON.stringify(value));
const descendants = (element) => element.childNodes.flatMap((item) => [item, ...descendants(item)]);
const control = (element, title) => descendants(element).find((item) => item.tagName === "label" && item._text === title)?.childNodes.find((item) => ["input", "select", "textarea"].includes(item.tagName));
const action = (element, title) => descendants(element).find((item) => item.tagName === "button" && item._text === title);

test("supply workspaces are real tab panels and keep legacy views / invite controls", () => {
  for (const id of ["products", "supplied", "discover", "documents", "installations", "submissions", "overview", "assets", "projects", "activity", "replication"])
    assert.match(html, new RegExp(`id="${id}"`));
  for (const id of ["join-project", "new-project", "invite-form", "join-form", "entity-form"])
    assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /value="incoming"/);
  assert.match(html, /value="changes-requested"/);
  assert.doesNotMatch(supplyScript, /privateKey|signingKey/);
});

test("large supply workspace loads immutable revisions once, never one request per record", async () => {
  const records = Array.from({ length: 120 }, (_, index) => ({
    ...source, id: "record-" + index, documents: [],
  }));
  const revisions = records.map((record) => ({ ...record, status: "issued" }));
  const { ui, calls } = environment((path) => {
    if (path.endsWith("/records")) return { items: records };
    if (path.endsWith("/revisions")) return { items: revisions };
    return { items: [] };
  });
  await ui.load();
  assert.equal(ui.sc.records.length, 120);
  assert.equal(ui.sc.revisions.length, 120);
  assert.equal(calls.filter((call) => call.path.endsWith("/revisions")).length, 1);
  assert.equal(calls.some((call) => /\/records\/[^/]+\/revisions/.test(call.path)), false);
  assert.equal(calls.length, 6);
});

test("authority and version pins cannot collide across organisations", () => {
  const { ui } = environment();
  assert.equal(ui.isOwned(source), true);
  assert.equal(ui.isOwned({ ...source, authorityDid: "did:web:public.example" }), false);
  assert.notEqual(ui.pinKey(source), ui.pinKey({ ...source, authorityDid: "did:web:other.example" }));
  assert.deepEqual(normalized(ui.sourcePin(source)), { authorityDid: source.authorityDid, recordId: source.id, revision: 1 });
});

test("accepting delivery is never presented as completed installation", () => {
  const { ui } = environment();
  assert.match(ui.technicalStatus({ ...source, status: "accepted", data: { status: "delivered" } }), /not an installation/);
  assert.match(ui.technicalStatus({ ...source, kind: "asset", status: "accepted", data: { status: "failed" } }), /failed.*independent of acceptance/);
});

test("source picker requires published product/offering revisions and accepted project supply", () => {
  const { ui } = environment();
  const records = [
    { id: "manufacturer", authorityDid: "did:web:m.example", kind: "product", revision: 1, status: "published" },
    { id: "draft", kind: "product", revision: 1, status: "draft" },
    { id: "supplier", kind: "offering", revision: 2, status: "published" },
    source,
    { ...source, id: "not-accepted", acceptedFrom: undefined },
    { ...source, id: "other-project", projectId: "other" },
  ];
  assert.deepEqual(normalized(ui.eligibleSources("offering", records)).map((item) => item.id), ["manufacturer", "supplier"]);
  assert.deepEqual(normalized(ui.eligibleSources("installation", records, null, "project-1")).map((item) => item.id), ["supply-1"]);
  assert.deepEqual(normalized(ui.eligibleSources("product", records, "manufacturer", null, "did:web:m.example")).map((item) => item.id), ["supplier"]);
  assert.deepEqual(normalized(ui.eligibleSources("product", records, "manufacturer", null, "did:web:other.example")).map((item) => item.id), ["manufacturer", "supplier"]);
});

test("installation allocations check exact quantity threshold, units and serial reuse", () => {
  const { ui } = environment();
  const pin = ui.sourcePin(source);
  const existing = [{ id: "installation-1", kind: "installation", sources: [{ ...pin, quantity: 2, serials: ["A", "B"] }] }];
  assert.doesNotThrow(() => ui.assertAllocation({ quantity: 1, unit: "each", serials: ["C"] }, [pin], [source], existing));
  assert.throws(() => ui.assertAllocation({ quantity: 1.01, unit: "each", serials: [] }, [pin], [source], existing), /exceeds/);
  assert.throws(() => ui.assertAllocation({ quantity: 1, unit: "each", serials: ["A"] }, [pin], [source], existing), /already allocated/);
  assert.throws(() => ui.assertAllocation({ quantity: 1, unit: "m", serials: [] }, [pin], [source]), /units/);
  assert.throws(() => ui.assertAllocation({ quantity: 1, unit: "each", serials: ["C", "C"] }, [pin], [source]), /unique/);
  assert.throws(() => ui.assertAllocation({ quantity: 0, unit: "each" }, [pin], [source]), /positive/);
  assert.throws(() => ui.assertAllocation({ quantity: 1, unit: "each" }, [{ ...pin, revision: 2 }], [source]), /immutable source revision is unavailable/);
  assert.doesNotThrow(() => ui.assertAllocation({ quantity: 3, unit: "each" }, [pin], [source], existing, "installation-1"));
});

test("fractional allocations compare exact JSON decimals without epsilon widening", () => {
  const { ui } = environment();
  const fractional = { ...source, data: { quantity: 0.3, unit: "m", serials: [] } };
  const pin = ui.sourcePin(fractional);
  const existing = [{ id: "fraction-1", kind: "installation", sources: [{ ...pin, quantity: 0.1, unit: "m" }] }];
  assert.doesNotThrow(() => ui.assertAllocation({ quantity: 0.2, unit: "m" }, [pin], [fractional], existing));
  assert.throws(() => ui.assertAllocation({ quantity: 0.20000000000000004, unit: "m" }, [pin], [fractional], existing), /exceeds/);
  const tiny = { ...fractional, data: { quantity: 3e-8, unit: "m" } };
  const tinyExisting = [{ id: "tiny-1", kind: "installation", sources: [{ ...pin, quantity: 1e-8, unit: "m" }] }];
  assert.doesNotThrow(() => ui.assertAllocation({ quantity: 2e-8, unit: "m" }, [pin], [tiny], tinyExisting));
  assert.throws(() => ui.assertAllocation({ quantity: Infinity, unit: "m" }, [pin], [fractional]), /positive/);
});

test("lineage cards use text nodes and public records expose reuse, never edit", () => {
  const { ui, get } = environment();
  ui.sc.catalogue = [{ ...source, id: "public", kind: "product", name: "<img onerror=alert(1)>", status: "published", acceptedFrom: undefined, documents: [] }];
  ui.renderCatalogue();
  const text = get("catalogue-list").textContent;
  assert.match(text, /<img onerror=alert\(1\)>/);
  assert.match(text, /Public \/ read-only source/);
  assert.match(text, /Reuse as my offering/);
  assert.doesNotMatch(text, /Edit data|Add document|Publish public revision/);
});

test("incoming/outgoing filters and project authority distinguish directed submissions", () => {
  const { ui, get } = environment();
  ui.sc.submissions = [
    { id: "in", direction: "incoming", senderDid: "did:web:sender.example", recipientDid: "did:web:local.example", projectId: "same", revision: 1, status: "issued" },
    { id: "out", direction: "outgoing", senderDid: "did:web:local.example", recipientDid: "did:web:remote.example", projectId: "same", revision: 2, status: "changes-requested", decision: { reason: "Correct quantity" } },
  ];
  ui.renderSubmissions();
  assert.match(get("submission-list").textContent, /issue in/);
  assert.doesNotMatch(get("submission-list").textContent, /issue out/);
  get("submission-direction").value = "outgoing";
  get("submission-project").value = JSON.stringify(["did:web:remote.example", "same"]);
  get("submission-status-filter").value = "changes-requested";
  ui.renderSubmissions();
  assert.match(get("submission-list").textContent, /Correct quantity/);
  assert.doesNotMatch(get("submission-list").textContent, /issue in/);
});

test("guided product form requires review, preserves data and sends local revision precondition", async () => {
  const { ui, get, calls } = environment();
  ui.sc.loaded = true; ui.sc.stale = false;
  const record = { id: "product-1", kind: "product", name: "Door", authorityDid: "did:web:local.example", revision: 4, status: "draft", projectId: null, ifcClass: "IfcDoorType", data: { model: "D1", properties: { fireRating: 60, certified: true } }, sources: [], documents: [] };
  ui.sc.records = [record];
  await ui.openRecord("product", record);
  assert.equal(get("supply-dialog-submit").hidden, true);
  await get("supply-dialog-next").onclick();
  assert.equal(get("supply-dialog-submit").hidden, false);
  assert.match(get("supply-dialog-body").textContent, /private working record/);
  await get("supply-form").onsubmit({ preventDefault() {} });
  const call = calls.find((item) => item.method === "PUT");
  assert.equal(call.path, "/clip/v1/supply-chain/records/product-1");
  assert.equal(call.body.expectedRevision, 4);
  assert.deepEqual(call.body.data.properties, { fireRating: 60, certified: true });
  assert.equal(call.body.projectId, null);
});

test("public publish preview omits private document bytes and requires confirmation", async () => {
  const { ui, get, calls } = environment();
  const record = { ...source, kind: "product", projectId: null, documents: [{ id: "private", name: "Private delivery", visibility: "private" }, { id: "public", name: "Datasheet", visibility: "public" }] };
  ui.openPublish(record);
  assert.match(get("supply-dialog-body").textContent, /Private delivery \/ private \/ omitted/);
  assert.match(get("supply-dialog-body").textContent, /Datasheet \/ public \/ bytes included/);
  await get("supply-form").onsubmit({ preventDefault() {} });
  assert.equal(calls.length, 0);
  assert.match(get("supply-dialog-error").textContent, /Confirm the public disclosure scope/);
});

test("refresh failure preserves prior data but explicitly marks it stale", async () => {
  const { ui, get } = environment(() => { const error = new Error("Dependency unavailable"); error.status = 424; throw error; });
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.records = [source];
  await assert.rejects(ui.load(true), /Dependency unavailable/);
  assert.equal(ui.sc.stale, true);
  assert.equal(ui.sc.records[0].id, "supply-1");
  assert.match(get("products-status").textContent, /Displayed data may be stale/);
  assert.equal(get("create-installation").disabled, true);
});

test("409 leaves user draft unapplied and offers explicit refresh rather than silent retry", async () => {
  const { ui, get, calls } = environment((path, method) => {
    if (method === "PUT") { const error = new Error("Record changed"); error.status = 409; throw error; }
    return { items: [] };
  });
  ui.sc.loaded = true; ui.sc.stale = false;
  const record = { ...source, kind: "product", projectId: null, data: { model: "Original" }, sources: [] };
  await ui.openRecord("product", record);
  await get("supply-dialog-next").onclick();
  await get("supply-form").onsubmit({ preventDefault() {} });
  assert.equal(calls.filter((item) => item.method === "PUT").length, 1);
  assert.equal(get("supply-dialog").open, true);
  assert.match(get("supply-dialog-error").textContent, /Refresh and compare/);
  assert.match(get("supply-dialog-error").textContent, /discard unsaved form/);
});

test("comparison reports changed and removed values without upgrading saved pins", () => {
  const { ui } = environment();
  assert.deepEqual(normalized(ui.compareData({ rating: 30, properties: { unit: "mm" }, old: true }, { rating: 60, properties: { unit: "mm" } })), ["rating: 30 -> 60", "old: true -> (removed)"]);
});

test("guided offering uses verified exact source pins and keeps supplier-owned values separate", async () => {
  const product = {
    id: "public-door", authorityDid: "did:web:manufacturer.example", kind: "product",
    revision: 2, status: "published", name: "Door model", ifcClass: "IfcDoorType",
    datasetId: "catalogue-1", graphPath: "product/door", sources: [], documents: [],
    data: { fireRating: 60, sku: "MANUFACTURER-SKU" },
  };
  const { ui, get, calls } = environment((path, method, body) => {
    if (path.endsWith("/dependencies/preview")) return { items: [product], digests: ["a".repeat(64)] };
    return { items: [] };
  });
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.catalogue = [product];
  await ui.openRecord("offering", null, product);
  control(get("supply-dialog-body"), "Supplier SKU").value = "SUPPLIER-SKU";
  await get("supply-dialog-next").onclick();
  assert.match(get("supply-dialog-body").textContent, /read-only source facts/);
  await get("supply-form").onsubmit({ preventDefault() {} });
  const call = calls.find((item) => item.path.endsWith("/records") && item.method === "POST");
  assert.equal(call.body.data.sku, "SUPPLIER-SKU");
  assert.equal(call.body.data.fireRating, undefined);
  assert.deepEqual(call.body.sources, [{
    authorityDid: product.authorityDid, recordId: product.id, revision: 2,
    digest: "a".repeat(64), datasetId: product.datasetId, entityPath: product.graphPath,
  }]);
  assert.ok(call.body.idempotencyKey);
});

test("accepted supply wizard creates a compatible installation with explicit allocation", async () => {
  const supply = { ...source, datasetId: "project-1", graphPath: "accepted/supply" };
  const { ui, get, calls } = environment((path) => {
    if (path.endsWith("/dependencies/preview")) return { items: [supply], digests: ["b".repeat(64)] };
    return { items: [] };
  });
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.revisions = [supply]; ui.sc.records = [supply];
  ui.sc.projects = [{ authorityDid: source.authorityDid, projectId: "project-1", name: "Building", local: true, visibility: "private" }];
  await ui.openRecord("installation", null, supply);
  const body = get("supply-dialog-body");
  control(body, "Installed / allocated quantity").value = "1";
  control(body, "Allocated serials (one per line)").value = "C";
  control(body, "Installation location").value = "Level 2 / room 7";
  await get("supply-dialog-next").onclick();
  await get("supply-form").onsubmit({ preventDefault() {} });
  const call = calls.find((item) => item.path.endsWith("/records") && item.method === "POST");
  assert.equal(call.body.kind, "installation");
  assert.equal(call.body.ifcClass, "IfcDoor");
  assert.equal(call.body.projectId, "project-1");
  assert.equal(call.body.data.status, "planned");
  assert.equal(call.body.sources[0].quantity, 1);
  assert.equal(call.body.sources[0].unit, "each");
  assert.deepEqual(call.body.sources[0].serials, ["C"]);
});

test("dependency preview mismatch blocks saving rather than changing the selected revision", async () => {
  const product = { ...source, kind: "product", status: "published", acceptedFrom: undefined };
  const { ui, get, calls } = environment((path) => path.endsWith("/dependencies/preview") ? { items: [{ ...product, revision: 2 }], digests: ["c".repeat(64)] } : { items: [] });
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.catalogue = [product];
  await ui.openRecord("offering", null, product);
  await get("supply-dialog-next").onclick();
  assert.equal(get("supply-dialog-submit").hidden, true);
  assert.match(get("supply-dialog-error").textContent, /does not match the selected revision/);
  assert.equal(calls.some((item) => item.path.endsWith("/records") && item.method === "POST"), false);
});

test("draft submission targets linked remote project and documents are opt-in grants", async () => {
  const record = { ...source, kind: "installation", documents: [{ id: "private-doc", name: "Inspection", visibility: "private", authorityDid: source.authorityDid }] };
  const draft = { id: "draft-1", senderDid: source.authorityDid, recipientDid: "did:web:client.example", projectId: "recipient-project", recordIds: [source.id], documentIds: [], revision: 1, status: "draft", direction: "outgoing" };
  const { ui, get, calls } = environment((path, method) => path.endsWith("/records/" + source.id) ? record : path.includes("/submissions") && method !== "GET" || path.endsWith("/submissions/draft-1") ? draft : { items: [] });
  ui.sc.records = [record]; ui.sc.loaded = true; ui.sc.stale = false;
  ui.sc.projects = [{ authorityDid: draft.recipientDid, projectId: draft.projectId, name: "Client building", local: false, visibility: "private" }];
  ui.openSubmission(record);
  get("supply-dialog-next").onclick();
  await get("supply-form").onsubmit({ preventDefault() {} });
  const call = calls.find((item) => item.path.endsWith("/submissions") && item.method === "POST");
  assert.equal(call.body.recipientDid, draft.recipientDid);
  assert.equal(call.body.projectId, draft.projectId);
  assert.deepEqual(call.body.recordIds, [record.id]);
  assert.deepEqual(call.body.documentIds, []);
  assert.equal(call.body.supersedes, null);
  assert.ok(call.body.idempotencyKey);
});

test("incoming whole-issue rejection requires reason and acceptance requires explicit review", async () => {
  const incoming = {
    id: "incoming-1", senderDid: "did:web:supplier.example", recipientDid: source.authorityDid,
    projectId: "project-1", recordIds: [source.id], documentIds: [], revision: 1, status: "issued",
    direction: "incoming", issue: { records: [source], created: "2026-10-02T10:00:00Z" },
  };
  const { ui, get, calls } = environment((path, method) => path.endsWith("/submissions/incoming-1") || method === "POST" ? incoming : { items: [] });
  await ui.openSubmissionDetail(incoming);
  await action(get("supply-dialog-body"), "Accept selected issue").onclick();
  assert.match(get("supply-dialog-error").textContent, /Review the evidence/);
  await action(get("supply-dialog-body"), "Reject with reason").onclick();
  assert.equal(calls.some((item) => item.method === "POST"), false);
  control(get("supply-dialog-body"), "Decision reason / requested correction").value = "Wrong batch";
  await action(get("supply-dialog-body"), "Reject with reason").onclick();
  const call = calls.find((item) => item.method === "POST");
  assert.equal(call.path, "/clip/v1/supply-chain/submissions/incoming-1/decision");
  assert.equal(call.body.reason, "Wrong batch");
  assert.equal(call.body.expectedRevision, 1);
  assert.equal(call.body.decision, "reject");
  assert.equal(call.body.recordIds, undefined);
});

test("property editor preserves numeric arrays/nulls and allows explicit property removal", async () => {
  const record = { ...source, kind: "product", projectId: null, sources: [], acceptedFrom: undefined, data: { properties: { measurements: [10, 20], optional: null, obsolete: true } } };
  const { ui, get, calls } = environment();
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.records = [record];
  await ui.openRecord("product", record);
  await action(get("supply-dialog-body"), "Remove property properties / obsolete").onclick();
  await get("supply-dialog-next").onclick();
  await get("supply-form").onsubmit({ preventDefault() {} });
  const payload = calls.find((item) => item.method === "PUT").body;
  assert.deepEqual(payload.data.properties.measurements, [10, 20]);
  assert.equal(payload.data.properties.optional, null);
  assert.equal(payload.data.properties.obsolete, undefined);
});

test("stale draft record revisions block issue and offer a new explicit reviewed draft", async () => {
  const draft = {
    id: "stale-draft", direction: "outgoing", senderDid: source.authorityDid, recipientDid: "did:web:client.example",
    projectId: "client-project", revision: 1, status: "draft", recordIds: [source.id], documentIds: [],
    recordRevisions: { [source.id]: 1 },
  };
  const fresh = { ...source, revision: 2 };
  const { ui, get } = environment((path) => path.endsWith("/submissions/stale-draft") ? draft : fresh);
  await ui.openSubmissionDetail(draft);
  assert.match(get("supply-dialog-body").textContent, /Cannot issue.*changed from revision 1 to 2/);
  assert.equal(action(get("supply-dialog-body"), "Issue immutable submission"), undefined);
  assert.ok(action(get("supply-dialog-body"), "Create fresh reviewed draft"));
});

test("source-owned document grants cannot be silently issued by a downstream recipient", () => {
  const document = { id: "upstream-doc", name: "Certificate", authorityDid: "did:web:manufacturer.example", recordId: "upstream-record", visibility: "private" };
  const { ui, get } = environment();
  const record = { ...source, kind: "installation", documents: [document] };
  ui.sc.records = [record];
  ui.sc.projects = [{ name: "Client project", projectId: "client-project", authorityDid: "did:web:client.example", visibility: "private", local: false }];
  ui.openSubmission(record);
  const inputs = descendants(get("supply-dialog-body")).filter((item) => item.tagName === "input" && item.type === "checkbox");
  assert.equal(inputs.at(-1).disabled, true);
  assert.equal(inputs.at(-1).checked, false);
  assert.match(get("supply-dialog-body").textContent, /original authority must grant the recipient access/);
});

test("shared API helper sends the local access key and preserves HTTP conflict status", async () => {
  const calls = [];
  const context = vm.createContext({
    $: () => ({ value: "local-access-key" }),
    fetch: async (path, options) => {
      calls.push({ path, options });
      return { ok: false, status: 409, text: async () => '{"detail":"Record changed; refresh before saving"}' };
    },
  });
  vm.runInContext(script.slice(script.indexOf("async function api("), script.indexOf("function readable(")), context);
  await assert.rejects(vm.runInContext('api("/clip/v1/supply-chain/records/product-1", "PUT", {expectedRevision:1})', context),
    (error) => error.status === 409 && /refresh/.test(error.message));
  assert.equal(calls[0].path, "/clip/v1/supply-chain/records/product-1");
  assert.equal(calls[0].options.headers["x-api-key"], "local-access-key");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), { expectedRevision: 1 });
});

test("authoring without authorization opens existing access dialog and performs no writes", async () => {
  const { ui, get, calls, state } = environment();
  get("key").value = "";
  state.authorized = false;
  await ui.openRecord("product");
  assert.equal(get("access-dialog").open, true);
  assert.match(get("access-error").textContent, /Local authorization/);
  assert.equal(calls.length, 0);
});

test("open demo mode loads private supply-chain records without an operator key", async () => {
  const { ui, get, calls, state } = environment();
  get("key").value = "";
  state.info.demoOpenAccess = true;
  state.authorized = true;
  await ui.load();
  assert.ok(calls.some((call) => call.path.endsWith("/records")));
  assert.ok(calls.some((call) => call.path.endsWith("/projects")));
  assert.ok(calls.some((call) => call.path.endsWith("/submissions")));
});

test("guided class validation rejects occurrence classes for product definitions", async () => {
  const { ui, get, calls } = environment();
  ui.sc.loaded = true; ui.sc.stale = false;
  await ui.openRecord("product");
  control(get("supply-dialog-body"), "IFC class").value = "IfcDoor";
  await get("supply-dialog-next").onclick();
  assert.match(get("supply-dialog-error").textContent, /ending in Type/);
  assert.equal(get("supply-dialog-submit").hidden, true);
  assert.equal(calls.length, 0);
});

test("accepted supply form locks upstream allocation facts and source pins", async () => {
  const { ui, get } = environment();
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.records = [source];
  ui.sc.projects = [{ authorityDid: source.authorityDid, projectId: source.projectId, name: "Project", local: true, visibility: "private" }];
  await ui.openRecord("supply", source);
  for (const title of ["IFC class", "Delivered quantity", "Unit", "Serial numbers (one per line)", "Selected immutable revision"])
    assert.equal(control(get("supply-dialog-body"), title).disabled, true, title);
  assert.match(get("supply-dialog-body").textContent, /Request a corrected supply issue/);
});

test("pending/rejected invitations are not eligible submission destinations", () => {
  const { ui, get } = environment();
  ui.sc.projects = [
    { authorityDid: "did:web:a.example", projectId: "pending", name: "Pending project", local: false, status: "pending" },
    { authorityDid: "did:web:a.example", projectId: "rejected", name: "Rejected project", local: false, status: "rejected" },
    { authorityDid: "did:web:a.example", projectId: "accepted", name: "Accepted project", local: false, status: "accepted" },
  ];
  assert.equal(ui.destinationEligible(ui.sc.projects[0]), false);
  assert.equal(ui.destinationEligible(ui.sc.projects[1]), false);
  ui.openSubmission();
  const options = control(get("supply-dialog-body"), "Recipient project destination").options;
  assert.equal(options.length, 1);
  assert.match(options[0].textContent, /Accepted project/);
});

test("scoped sender approval sends revision precondition without Contributor or graph grants", async () => {
  const { ui, get, calls } = environment(() => ({ projectId: "project-1", revision: 0, senders: [] }));
  await ui.openSenders({ projectId: "project-1", name: "Building" });
  assert.match(get("supply-dialog-body").textContent, /without broad graph-read or Contributor access/);
  control(get("supply-dialog-body"), "Approved sender organisation DIDs (one per line)").value = "did:web:supplier.example";
  await get("supply-form").onsubmit({ preventDefault() {} });
  const call = calls.find((item) => item.method === "PUT");
  assert.equal(call.path, "/clip/v1/supply-chain/projects/project-1/senders");
  assert.deepEqual(call.body, { expectedRevision: 0, senders: ["did:web:supplier.example"] });
});

test("duplicate sender approval input is rejected before any permissions write", async () => {
  const { ui, get, calls } = environment(() => ({ projectId: "project-1", revision: 3, senders: [] }));
  await ui.openSenders({ projectId: "project-1", name: "Building" });
  control(get("supply-dialog-body"), "Approved sender organisation DIDs (one per line)").value = "did:web:s.example\ndid:web:s.example";
  await get("supply-form").onsubmit({ preventDefault() {} });
  assert.equal(calls.some((item) => item.method === "PUT"), false);
  assert.match(get("supply-dialog-error").textContent, /distinct did:web/);
});

test("schema classes are suggestions and do not reject other concrete IFC classes", async () => {
  const { ui, get } = environment();
  ui.sc.loaded = true; ui.sc.stale = false;
  ui.sc.schema = { typeClasses: ["IfcDoorType"], occurrenceClasses: ["IfcDoor"] };
  await ui.openRecord("product");
  control(get("supply-dialog-body"), "IFC class").value = "IfcWindowType";
  await get("supply-dialog-next").onclick();
  assert.equal(get("supply-dialog-error").textContent, "");
  assert.equal(get("supply-dialog-submit").hidden, false);
  assert.match(get("supply-dialog-body").textContent, /IfcWindowType/);
});

test("explicit private freeze sends expected revision without publication or delivery", async () => {
  const { ui, get, calls } = environment();
  ui.openFreeze(source);
  assert.match(get("supply-dialog-body").textContent, /does not publish, deliver, accept or install/);
  await get("supply-form").onsubmit({ preventDefault() {} });
  const write = calls.find((item) => item.method === "POST");
  assert.equal(write.path, "/clip/v1/supply-chain/records/supply-1/revisions");
  assert.deepEqual(write.body, { expectedRevision: 1 });
  assert.equal(calls.some((item) => /publish|submissions/.test(item.path) && item.method === "POST"), false);
});

test("delivery retry reuses exact decision/issue request despite refreshed outer revision", () => {
  const { ui } = environment();
  const original = ui.deliveryRequest("decision", "issue-1", { decision: "accept", reason: "", expectedRevision: 1 });
  const retry = ui.deliveryRequest("decision", "issue-1", { decision: "accept", reason: "", expectedRevision: 2 });
  assert.deepEqual(normalized(retry), normalized(original));
  assert.equal(retry.expectedRevision, 1);
  const issue = ui.deliveryRequest("issue", "outgoing-1", { expectedRevision: 1 });
  assert.deepEqual(normalized(ui.deliveryRequest("issue", "outgoing-1", { expectedRevision: 2 })), normalized(issue));
});

test("generated select accessible names exactly match their labels rather than option text", async () => {
  const { ui, get } = environment();
  ui.sc.loaded = true; ui.sc.stale = false; ui.sc.revisions = [source]; ui.sc.records = [source];
  ui.sc.projects = [{ authorityDid: source.authorityDid, projectId: source.projectId, name: "Project", local: true, visibility: "private" }];
  await ui.openRecord("installation", null, source);
  const body = get("supply-dialog-body");
  for (const title of ["Selected immutable revision", "Technical status", "Sourcing route", "Private local project"]) {
    const select = control(body, title);
    assert.equal(select.tagName, "select");
    assert.equal(select.attributes["aria-label"], title);
    assert.ok(select.options.length > 0);
  }
});

test("native product adoption preserves selected address and uses verified authority sequence", async () => {
  const candidate = { datasetId: "private-library", entityPath: "types/door", name: "Native Door", ifcClass: "IfcDoorType", properties: { rating: 60 }, projectName: "Private library", visibility: "private" };
  const adopted = { ...source, id: "adopted", name: candidate.name, kind: "product", projectId: null, sources: [], acceptedFrom: undefined, datasetId: candidate.datasetId, graphPath: candidate.entityPath, data: { nativeProperties: candidate.properties } };
  const { ui, get, calls } = environment((path, method) => {
    if (path.endsWith("/adoption-candidates")) return { authorityDid: source.authorityDid, sequence: 17, items: [candidate] };
    if (path.endsWith("/adopt") && method === "POST") return adopted;
    return { items: [] };
  });
  await ui.openAdopt();
  assert.match(get("supply-dialog-body").textContent, /does not copy or edit imported types/);
  const confirm = descendants(get("supply-dialog-body")).find((item) => item.type === "checkbox");
  confirm.checked = true;
  await get("supply-form").onsubmit({ preventDefault() {} });
  const write = calls.find((item) => item.path.endsWith("/records/adopt") && item.method === "POST");
  assert.equal(write.body.datasetId, candidate.datasetId);
  assert.equal(write.body.entityPath, candidate.entityPath);
  assert.equal(write.body.expectedSequence, 17);
  assert.equal(write.body.kind, "product");
  assert.ok(write.body.idempotencyKey);
});

test("native adoption fails closed for public or foreign candidate responses", async () => {
  const { ui } = environment(() => ({ authorityDid: "did:web:other.example", sequence: 0, items: [] }));
  await assert.rejects(ui.openAdopt(), /invalid authority/);
  const publicEnv = environment(() => ({ authorityDid: source.authorityDid, sequence: 1, items: [{ datasetId: "public", entityPath: "type", visibility: "public" }] }));
  await assert.rejects(publicEnv.ui.openAdopt(), /private ownership/);
});

test("empty adoption picker does not leave subsequent document forms disabled", async () => {
  const { ui, get } = environment(() => ({ authorityDid: source.authorityDid, sequence: 0, items: [] }));
  await ui.openAdopt();
  assert.equal(get("supply-dialog-submit").disabled, true);
  ui.openDocument(source);
  assert.equal(get("supply-dialog-submit").disabled, false);
});

test("replace document picker excludes upstream evidence and sends current revision", async () => {
  const local = { id: "old-local", recordId: source.id, authorityDid: source.authorityDid, name: "Old datasheet", visibility: "private" };
  const upstream = { ...local, id: "upstream", recordId: "remote-source", authorityDid: "did:web:manufacturer.example" };
  const record = { ...source, documents: [local, upstream] };
  const { ui, get, calls } = environment();
  ui.openDocument(record);
  const body = get("supply-dialog-body");
  const picker = control(body, "Replace existing document (optional)");
  assert.equal(picker.options.length, 2);
  assert.equal(picker.options.some((item) => item.value === upstream.id), false);
  picker.value = local.id;
  control(body, "Document file").files = [{ name: "New.pdf", size: 4, type: "application/pdf" }];
  control(body, "Document name").value = "New datasheet";
  await get("supply-form").onsubmit({ preventDefault() {} });
  const write = calls.find((item) => item.method === "POST");
  assert.equal(write.body.replacesDocumentId, local.id);
  assert.equal(write.body.expectedRevision, record.revision);
  assert.equal(write.body.content, "dGVzdA==");
  assert.ok(write.body.idempotencyKey);
});

test("retirement requires confirmation and detaches current local reference without grant changes", async () => {
  const local = { id: "local-doc", recordId: source.id, authorityDid: source.authorityDid, name: "Certificate", visibility: "private" };
  const { ui, get, calls } = environment();
  assert.equal(ui.canManageAttachment(source, local), true);
  assert.equal(ui.canManageAttachment(source, { ...local, authorityDid: "did:web:other.example" }), false);
  assert.throws(() => ui.openRetireDocument(source, { ...local, recordId: "upstream" }), /original locally owned/);
  ui.openRetireDocument(source, local);
  assert.match(get("supply-dialog-body").textContent, /stored bytes are not deleted.*grants are unaffected/);
  await get("supply-form").onsubmit({ preventDefault() {} });
  assert.equal(calls.length, 0);
  descendants(get("supply-dialog-body")).find((item) => item.type === "checkbox").checked = true;
  await get("supply-form").onsubmit({ preventDefault() {} });
  const write = calls.find((item) => item.method === "POST");
  assert.equal(write.path, "/clip/v1/supply-chain/records/supply-1/documents/local-doc/detach");
  assert.equal(write.body.expectedRevision, 1);
  assert.ok(write.body.idempotencyKey);
  assert.equal(calls.some((item) => item.path.includes("/grants")), false);
});

test("separate document upload does not imply replacement and defaults to private evidence", async () => {
  const { ui, get, calls } = environment();
  ui.openDocument(source);
  const body = get("supply-dialog-body");
  control(body, "Document file").files = [{ name: "Certificate.pdf", size: 4 }];
  control(body, "Document name").value = "Certificate";
  await get("supply-form").onsubmit({ preventDefault() {} });
  const write = calls.find((item) => item.method === "POST");
  assert.equal(Object.hasOwn(write.body, "replacesDocumentId"), false);
  assert.equal(write.body.visibility, "private");
});
