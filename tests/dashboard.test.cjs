const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const script = readFileSync(
  join(__dirname, "../node/app/static/dashboard.js"),
  "utf8",
);
test("console separates CLIP infrastructure from IFC graph routes without old aliases", () => {
  for (const route of [
    "/clip/v1/node/info", "/clip/v1/network/gossip/peers", "/clip/v1/replication/status",
    "/clip/v1/projects", "/clip/v1/supply-chain", "/ifc/v1/datasets",
    "/ifc/v1/projects/templates",
  ]) {
    assert.ok(script.includes(route), route);
  }
  assert.ok(script.includes('api("/ifc/v1" + path'));
  assert.ok(!script.includes("/ifcx/v1"));
  assert.ok(script.includes('ifcxVersion: "ifcx_alpha"'));
});
test("canonical manufacturer definitions share identity across projects but physical assets do not", () => {
  const result = evaluate(`
    const schema="urn:clip:construction:product-identity:v1";
    const make=(dataset)=>({authorityDid:'did:web:client.example',datasetId:dataset,
      productResolution:{mode:'current-published'},
      entities:{shared:{components:{[schema]:{authorityDid:'did:web:manufacturer.example',recordId:'motor',revision:1}}},
        pump:{components:{}}},
      effectiveComponents:{shared:{[schema]:{authorityDid:'did:web:manufacturer.example',recordId:'motor',revision:1}},
        pump:{[schema]:{authorityDid:'did:web:manufacturer.example',recordId:'motor',revision:1}}}});
    const first=make('project-a'),second=make('project-b');
    state.graphs=[first,second];
    return {sameType:entityKey(first,'shared')===entityKey(second,'shared'),
      differentAssets:entityKey(first,'pump')!==entityKey(second,'pump'),
      types:inventory().entries.filter(entry=>entry.path==='shared').length};
  `);
  assert.equal(result.sameType, true);
  assert.equal(result.differentAssets, true);
  assert.equal(result.types, 1);
});
test("pinned manufacturer versions are distinct from a shared current definition", () => {
  const result = evaluate(`
    const make=(revision)=>({productResolution:{mode:'pinned'},
      entities:{shared:{components:{'urn:clip:construction:product-identity:v1':
        {authorityDid:'did:web:manufacturer.example',recordId:'motor',revision}}}}});
    return entityKey(make(1),'shared')!==entityKey(make(2),'shared');
  `);
  assert.equal(result, true);
});
function evaluate(code, globals = {}) {
  const context = vm.createContext(globals);
  vm.runInContext(
    script.slice(0, script.indexOf("function activateView")),
    context,
  );
  vm.runInContext(
    script.slice(script.indexOf("function openProjectDialog()"), script.indexOf('$("new-project").onclick')),
    context,
  );
  return JSON.parse(
    vm.runInContext(`JSON.stringify((()=>{${code}})())`, context),
  );
}
const setup = `
state.info={did:'did:web:owner.example',role:'owner'};
const graph={authorityDid:state.info.did,datasetId:'urn:building',schemas:{'clip::installation-reference':{value:{dataType:'Reference'}}},sources:[{publisherDid:'did:web:manufacturer.example',datasetId:'urn:types',entityPaths:['types/door']}],entities:{building:{children:{door:'building/door-1'}},'building/door-1':{inherits:{type:'types/door'}},'types/door':{},'events/install':{}},effectiveComponents:{building:{},'building/door-1':{'ifc::name':'Door','clip::installation-reference':'events/install'},'types/door':{'ifc::name':'Door type'},'events/install':{'clip::installation-event':{status:'complete'}}}};
state.graphs=[graph];state.history=[{datasetId:'urn:building',transaction:{proposal:{actorDid:'did:web:contractor.example',target:{entityPath:'events/install'}}}}];
`;
test("facility containment does not classify contained assets as buildings", () => {
  const result = evaluate(
    setup +
      `const {entries}=inventory();return entries.map(entry=>({path:entry.path,kind:entry.kind,parent:entry.parent?.path}));`,
  );
  assert.equal(
    result.find((entry) => entry.path === "building").kind,
    "facility",
  );
  assert.equal(
    result.find((entry) => entry.path === "building/door-1").kind,
    "asset",
  );
  assert.equal(
    result.find((entry) => entry.path === "building/door-1").parent,
    "building",
  );
  assert.equal(
    result.find((entry) => entry.path === "types/door").kind,
    "type",
  );
  assert.equal(
    result.find((entry) => entry.path === "events/install").kind,
    "event",
  );
});
test("asset origins include inherited product data and referenced accepted events", () => {
  const result = evaluate(
    setup +
      `const entry=inventory().entries.find(entry=>entry.kind==='asset');return {origins:entitySources(entry).map(source=>source.did),status:assetStatus(entry)};`,
  );
  assert.deepEqual(result.origins.sort(), [
    "did:web:contractor.example",
    "did:web:manufacturer.example",
  ]);
  assert.equal(result.status, "Complete");
});
test("peer membership is not presented as imported data", () => {
  const result = evaluate(
    setup +
      `state.peers=[{did:'did:web:relay.example',status:'alive'}];const filtered=origins().map(origin=>origin.did);state.allPeers=true;return {filtered,peer:origins().find(origin=>origin.did==='did:web:relay.example').imports.length};`,
  );
  assert.equal(result.filtered.includes("did:web:relay.example"), false);
  assert.equal(result.peer, 0);
});

test("entity network distinguishes containment and type inheritance", () => {
  const result = evaluate(setup + `const data=entityNetworkData();return {count:data.entries.length,kinds:data.links.map(link=>link.kind)};`);
  assert.equal(result.count, 4);
  assert.deepEqual(result.kinds.sort(), ["Contains", "Type"]);
});

test("entity identities include authority to avoid cross-node collisions", () => {
  const result = evaluate(`return entityKey({authorityDid:'did:web:a.example',datasetId:'same'},'asset')!==entityKey({authorityDid:'did:web:b.example',datasetId:'same'},'asset');`);
  assert.equal(result, true);
});

test("graph proposal history contributes to entity provenance", () => {
  const result = evaluate(setup + `state.history.push({datasetId:'urn:building',transaction:{proposal:{actorDid:'did:web:author.example',operations:[{action:'create',node:{path:'building/door-1'}}]}}});const entry=inventory().entries.find(entry=>entry.kind==='asset');return entitySources(entry).map(source=>source.did);`);
  assert.equal(result.includes("did:web:author.example"), true);
});

test("join acceptance is available only for pending, unexpired invites", () => {
  const result = evaluate(`const now=Date.parse('2026-10-02T12:00:00Z');const request={status:'pending',inviteStatus:'pending',expiresAt:'2026-10-03T12:00:00Z'};return [canAcceptJoin(request,now),canAcceptJoin({...request,status:'accepted'},now),canAcceptJoin({...request,inviteStatus:'revoked'},now),canAcceptJoin({...request,expiresAt:'2026-10-01T12:00:00Z'},now)];`);
  assert.deepEqual(result, [true, false, false, false]);
});

test("Create Project requests access before opening its form", () => {
  const elements = {};
  const document = {
    getElementById(id) {
      return elements[id] ||= {
        opened: false,
        setAttribute() {},
        reset() {},
        showModal() { this.opened = true; },
      };
    },
  };
  const result = evaluate(`openProjectDialog();return {pending:state.pendingProject,access:$("access-dialog").opened,project:$("project-dialog").opened};`, { document });
  assert.deepEqual(result, { pending: true, access: true, project: false });
});

test("authorized Create Project opens a private project form", () => {
  const elements = {};
  const document = {
    getElementById(id) {
      return elements[id] ||= {
        opened: false,
        setAttribute() {},
        reset() {},
        showModal() { this.opened = true; },
      };
    },
  };
  const result = evaluate(`state.authorized=true;openProjectDialog();return {project:$("project-dialog").opened,kind:$("project-kind").value,public:$("project-public").checked};`, { document });
  assert.deepEqual(result, { project: true, kind: "project", public: false });
});
