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

test("a type catalogue container is not counted as another product type", () => {
  const result = evaluate(`
    state.graphs=[{authorityDid:'did:web:m.example',datasetId:'catalog',
      entities:{types:{children:{pump:'types/pump'}},'types/pump':{}},
      effectiveComponents:{types:{},'types/pump':{}}}];
    return inventory().entries.map(entry=>({path:entry.path,kind:entry.kind}));
  `);
  assert.deepEqual(result, [{path:"types",kind:"container"},{path:"types/pump",kind:"type"}]);
});

test("canonical type authority is the manufacturer, while installed assets retain local authority", () => {
  const result = evaluate(`
    const schema='urn:clip:construction:product-identity:v1';
    const graph={authorityDid:'did:web:owner.example',entities:{
      type:{components:{[schema]:{authorityDid:'did:web:manufacturer.example',recordId:'door'}}},
      asset:{components:{}}}};
    return {type:entityAuthority({graph,path:'type'}),asset:entityAuthority({graph,path:'asset'})};
  `);
  assert.deepEqual(result, { type: "did:web:manufacturer.example", asset: "did:web:owner.example" });
});

test("every entity owner label uses record authority, not inherited manufacturer or event actor", () => {
  const result = evaluate(`
    state.info={did:'did:web:owner.example',role:'owner'};
    const product='urn:clip:construction:product-identity:v1',data='urn:clip:construction:manufacturer-data:v1';
    const graph={authorityDid:state.info.did,entities:{
      type:{components:{[product]:{authorityDid:'did:web:aster.example',recordId:'motor'}}},
      asset:{components:{}},campus:{components:{}},event:{components:{}},
      shared:{components:{'urn:clip:construction:entity-identity:v1':{
        authorityDid:'did:web:facility.example',datasetId:'spatial',entityPath:'campus'}}}},
      effectiveComponents:{type:{[data]:{data:{manufacturer:'Aster'}}},
        asset:{[product]:{authorityDid:'did:web:aster.example'}},campus:{},
        event:{event:{actorDid:'did:web:inspector.example'}},shared:{}}};
    const snapshot={...graph,authorityDid:'did:web:supplier.example'};
    return {type:entityOwnerLabel({graph,path:'type'}),asset:entityOwnerLabel({graph,path:'asset'}),
      campus:entityOwnerLabel({graph,path:'campus'}),event:entityOwnerLabel({graph,path:'event'}),
      shared:entityOwnerLabel({graph,path:'shared'}),snapshot:entityOwnerLabel({graph:snapshot,path:'event',snapshot:true})};
  `);
  assert.deepEqual(result, {type:"Data owner: Aster / aster.example",asset:"Data owner: Owner",
    campus:"Data owner: Owner",event:"Data owner: Owner",shared:"Data owner: facility.example",
    snapshot:"Data owner: supplier.example"});
  assert.ok(script.includes('.attr("class", "entity-node-owner")'));
});

test("shared types retain relationships from every dataset without merging installations", () => {
  const result = evaluate(`
    const identity="urn:clip:construction:product-identity:v1";
    const make=(datasetId,path,serial)=>({
      authorityDid:'did:web:owner.example',datasetId,
      entities:{catalog:{children:{product:path}},[path]:{components:{[identity]:
        {authorityDid:'did:web:manufacturer.example',recordId:'door',revision:1}}},
        room:{children:{door:'door'}},door:{components:{},inherits:{type:path}}},
      effectiveComponents:{catalog:{},[path]:{'ifc::name':'Door type'},
        room:{},door:{'ifc::name':'Door type','ifc::serial':serial}}
    });
    state.graphs=[make('a','types/door','A'),make('b','types/door','B')];
    const network=entityNetworkData();
    return {types:network.entries.filter(entry=>entry.kind==='type').length,
      assets:network.entries.filter(entry=>entry.path==='door').map(entry=>entry.label),
      typeLinks:network.links.filter(link=>link.kind==='Type').length,
      listLinks:network.links.filter(link=>link.kind==='Lists type').length};
  `);
  assert.equal(result.types, 1);
  assert.deepEqual(result.assets, ["Door type / A", "Door type / B"]);
  assert.equal(result.typeLinks, 2);
  assert.equal(result.listLinks, 2);
});

test("supply allocations are not type inheritance and direct/supplier routes remain distinct", () => {
  const result = evaluate(`
    const identity="urn:clip:construction:product-identity:v1",source="urn:clip:construction:source:v1";
    const manufacturer='did:web:manufacturer.example',owner='did:web:owner.example';
    const workflow=(kind,lineage=[],sources=[])=>({[source]:{class:kind==='installation'?'IfcPump':'IfcPumpType',
      properties:{supplyChain:{kind,lineage,sources,data:{status:'commissioned'}}}}});
    const graph={authorityDid:owner,datasetId:'project',entities:{
      type:{components:{[identity]:{authorityDid:manufacturer,recordId:'pump',revision:1},
        [source]:{class:'IfcPumpType'}}},
      motor:{components:{[identity]:{authorityDid:manufacturer,recordId:'motor',revision:1},
        [source]:{class:'IfcPumpType'}}},
      direct:{components:workflow('supply',[{kind:'offering',authorityDid:manufacturer}]),inherits:{manufacturerType:'type'}},
      supplier:{components:workflow('supply',[{kind:'offering',authorityDid:manufacturer},
        {kind:'offering',authorityDid:'did:web:supplier.example'}]),inherits:{manufacturerType:'type'}},
      pump:{components:workflow('installation',[],[{authorityDid:owner,datasetId:'project',entityPath:'supplier'}]),
        inherits:{type:'supplier',manufacturerType:'type'}}},
      effectiveComponents:{}};
    graph.entities.type.components['urn:clip:construction:component-types:v1']=['motor'];
    for(const [path,entity] of Object.entries(graph.entities)) graph.effectiveComponents[path]=entity.components;
    state.graphs=[graph];
    const network=entityNetworkData(),pump=network.entries.find(entry=>entry.path==='pump');
    return {links:network.links.map(link=>({kind:link.kind,label:link.label,
      from:network.entries.find(entry=>entry.key===link.source).path,
      to:network.entries.find(entry=>entry.key===link.target).path})),status:assetStatus(pump),kind:pump.kind};
  `);
  assert.equal(result.kind, "asset");
  assert.equal(result.status, "Commissioned");
  assert.deepEqual(result.links.filter(link => link.from === "pump").map(link => [link.kind, link.to]),
    [["Allocated from", "supplier"], ["Type", "type"]]);
  assert.ok(result.links.some(link => link.from === "direct" && link.label === "Direct supply"));
  assert.ok(result.links.some(link => link.from === "supplier" && link.label === "Supply via supplier.example"));
  assert.ok(result.links.some(link => link.kind === "Component type" && link.to === "motor"));
});

test("same-named but independently identified products are not merged", () => {
  const result = evaluate(`
    const schema="urn:clip:construction:product-identity:v1";
    const graph={authorityDid:'did:web:owner.example',datasetId:'project',entities:{
      'types/a':{components:{[schema]:{authorityDid:'did:web:m.example',recordId:'a'}}},
      'types/b':{components:{[schema]:{authorityDid:'did:web:m.example',recordId:'b'}}}},
      effectiveComponents:{'types/a':{'ifc::name':'Same name'},'types/b':{'ifc::name':'Same name'}}};
    state.graphs=[graph];return inventory().entries.length;
  `);
  assert.equal(result, 2);
});

test("non-product inheritance remains visible without being called a product type", () => {
  const result = evaluate(`
    const graph={authorityDid:'did:web:owner.example',datasetId:'project',
      entities:{asset:{inherits:{template:'template'}},template:{}},
      effectiveComponents:{asset:{},template:{}}};
    state.graphs=[graph];return entityNetworkData().links.map(link=>link.kind);
  `);
  assert.deepEqual(result, ["Inherits"]);
});

test("a manufacturer's working record and resolved definition represent one type, never a self-edge", () => {
  const result = evaluate(`
    const source="urn:clip:construction:source:v1",identity="urn:clip:construction:product-identity:v1";
    const graph={authorityDid:'did:web:m.example',datasetId:'products',productResolution:{
      mode:'current-published',definitions:[{authorityDid:'did:web:m.example',recordId:'pump',revision:2}]},
      entities:{record:{components:{[source]:{class:'IfcPumpType',properties:{supplyChain:{
        kind:'product',authorityDid:'did:web:m.example',id:'pump',revision:2}}}},inherits:{manufacturerType:'definition'}},
        definition:{components:{[identity]:{authorityDid:'did:web:m.example',recordId:'pump',revision:2},
          [source]:{class:'IfcPumpType'}}}},effectiveComponents:{record:{},definition:{}}};
    state.graphs=[graph];const network=entityNetworkData();return {paths:network.entries.map(entry=>entry.path),links:network.links.length};
  `);
  assert.deepEqual(result, { paths: ["definition"], links: 0 });
});

test("lineage follows upstream sources and components, not other installations or room siblings", () => {
  const result = evaluate(`
    const entries=['pump','standby','door','delivery','supplier','contractor','type','motor','room','building','catalog']
      .map(key=>({key,kind:key==='type'||key==='motor'?'type':'asset'}));
    const link=(source,target,kind)=>({source,target,kind});
    const network={entries,links:[link('pump','delivery','Allocated from'),link('pump','type','Type'),
      link('delivery','contractor','Received from'),link('contractor','supplier','Sourced from'),
      link('supplier','type','Sourced from'),link('type','motor','Component type'),
      link('standby','type','Type'),link('door','room','Inherits'),
      link('room','pump','Contains'),link('room','door','Contains'),
      link('building','room','Contains'),link('catalog','type','Lists type')]};
    const lineage=lineageNetworkData(network,'pump');
    return {keys:lineage.entries.map(entry=>entry.key),links:lineage.links.map(link=>link.kind),
      typeUsages:lineageNetworkData(network,'type').entries.map(entry=>entry.key),
      missing:lineageNetworkData(network,'missing').entries.length};
  `);
  assert.deepEqual(result.keys.sort(), ["building", "contractor", "delivery", "motor", "pump", "room", "supplier", "type"]);
  assert.ok(!result.links.includes("Lists type"));
  assert.ok(result.typeUsages.includes("standby"));
  assert.ok(!result.typeUsages.includes("door"));
  assert.equal(result.missing, 0);
});

test("lineage traversal terminates on sourcing and containment cycles", () => {
  const result = evaluate(`
    const network={entries:[{key:'a'},{key:'b'},{key:'room'}],links:[
      {source:'a',target:'b',kind:'Sourced from'},{source:'b',target:'a',kind:'Sourced from'},
      {source:'room',target:'a',kind:'Contains'},{source:'a',target:'room',kind:'Contains'}]};
    return lineageNetworkData(network,'a').entries.map(entry=>entry.key);
  `);
  assert.deepEqual(result, ["a", "b", "room"]);
});

test("visual arrows flow from component, product, delivery and event into their consuming entity", () => {
  const result = evaluate(`
    return ['Type','Component type','Allocated from','Sourced from','Received from','Event','Contains','Lists type']
      .map(kind=>entityFlowLink({source:'consumer',target:'input',kind}));
  `);
  for (const link of result) {
    const containment = ["Contains", "Lists type"].includes(link.kind);
    assert.equal(link.source, containment ? "consumer" : "input");
    assert.equal(link.target, containment ? "input" : "consumer");
  }
});

test("project installations reference one shared spatial model without importing sibling assets", () => {
  const result = evaluate(`
    const schema='urn:clip:construction:source:v1';
    const spatial={authorityDid:'did:web:owner.example',datasetId:'spatial',
      entities:{campus:{children:{building:'building'}},building:{children:{room:'room'}},
        room:{children:{existing:'existing'}},existing:{},unrelated:{}},
      effectiveComponents:{campus:{'ifc::name':'Campus'},building:{},room:{},existing:{},unrelated:{}}};
    const project={authorityDid:spatial.authorityDid,datasetId:'project',entities:{pump:{
      components:{[schema]:{class:'IfcPump',properties:{locationReference:{
        authorityDid:spatial.authorityDid,datasetId:'spatial',entityPath:'room'}}}}}},
      effectiveComponents:{pump:{[schema]:{class:'IfcPump'}}}};
    state.graphs=[spatial,project];state.scope='project';
    const network=entityNetworkData(),asset=inventory().entries.find(entry=>entry.path==='pump');
    return {paths:network.entries.map(entry=>entry.path),parent:asset.parent.path,
      contains:network.links.filter(link=>link.kind==='Contains').length,
      depths:[...lineageNetworkData(network,asset.key).depths.values()]};
  `);
  assert.deepEqual(result.paths.sort(), ["building", "campus", "pump", "room"]);
  assert.equal(result.parent, "room");
  assert.equal(result.contains, 3);
  assert.deepEqual(result.depths, [0, -1, -2, -3]);
});

test("missing referenced installation locations are explicitly reported, not silently dropped", () => {
  const result = evaluate(`
    const source='urn:clip:construction:source:v1';
    state.graphs=[{authorityDid:'did:web:owner.example',datasetId:'project',entities:{
      asset:{components:{[source]:{properties:{locationReference:{
        authorityDid:'did:web:owner.example',datasetId:'missing',entityPath:'room'}}}}}}}];
    try { inventory();return ''; } catch(error) {return error.message;}
  `);
  assert.match(result, /Referenced installation location is unavailable.*missing.*room/);
});

test("shared entity addresses merge contributions, never names, and retain all relationships and origins", () => {
  const result = evaluate(`
    const schema='urn:clip:construction:entity-identity:v1';
    const identity={authorityDid:'did:web:owner.example',datasetId:'spatial',entityPath:'campus'};
    const make=(datasetId,actor)=>({authorityDid:identity.authorityDid,datasetId,
      entities:{campus:{components:{[schema]:identity},children:{asset:'asset'}},asset:{components:{}}},
      effectiveComponents:{campus:{'ifc::name':'Campus'},asset:{}},sources:[],schemas:{}});
    state.graphs=[make('a'),make('b')];
    state.history=['a','b'].map((datasetId,index)=>({datasetId,transaction:{proposal:{
      actorDid:'did:web:contributor'+index+'.example',target:{entityPath:'campus'}}}}));
    const network=entityNetworkData(),campus=inventory().entries.find(entry=>entry.path==='campus');
    return {campuses:network.entries.filter(entry=>entry.path==='campus').length,
      assets:network.entries.filter(entry=>entry.path==='asset').length,
      contributions:campus.contributions.length,origins:entitySources(campus).length,
      contains:network.links.filter(link=>link.kind==='Contains').length};
  `);
  assert.deepEqual(result, { campuses: 1, assets: 2, contributions: 2, origins: 2, contains: 2 });
});

test("work events flow into their subjects and are retained in asset lineage", () => {
  const result = evaluate(`
    const graph={authorityDid:'did:web:owner.example',datasetId:'project',schemas:{},
      entities:{pump:{components:{}},'events/check':{components:{
        'urn:clip:construction:event:v1':{kind:'inspection',subject:'pump',status:'complete'}}}},
      effectiveComponents:{pump:{},'events/check':{'urn:clip:construction:event:v1':{kind:'inspection'}}}};
    state.graphs=[graph];const network=entityNetworkData(),asset=network.entries.find(entry=>entry.path==='pump');
    return {flow:network.links.map(entityFlowLink).map(link=>({
      from:network.entries.find(entry=>entry.key===link.source).path,
      to:network.entries.find(entry=>entry.key===link.target).path,kind:link.kind})),
      lineage:lineageNetworkData(network,asset.key).entries.map(entry=>entry.path)};
  `);
  assert.deepEqual(result.flow, [{ from: "events/check", to: "pump", kind: "Event" }]);
  assert.deepEqual(result.lineage, ["pump", "events/check"]);
});

test("layout movement persists through rendering state but is isolated by scope and lineage", () => {
  const result = evaluate(`
    moveEntity('pump',210,90);moveEntity('pump',NaN,50);
    const full=entityLayout().positions.get('pump');
    state.lineage='pump';moveEntity('pump',500,120);
    const lineage=entityLayout().positions.get('pump');
    state.scope='another';const other=entityLayout().positions.has('pump');
    state.scope='';state.lineage=null;
    return {full,lineage,other,restored:entityLayout().positions.get('pump')};
  `);
  assert.deepEqual(result, { full: { x: 210, y: 90 }, lineage: { x: 500, y: 120 }, other: false,
    restored: { x: 210, y: 90 } });
});

test("issued upstream snapshots expose exact lineage without becoming local editable assets", () => {
  const result = evaluate(`
    const schema='urn:clip:construction:source:v1',identity='urn:clip:construction:product-identity:v1';
    const manufacturer='did:web:m.example',owner='did:web:o.example',supplier='did:web:s.example';
    const snapshot={id:'offer',authorityDid:supplier,revision:2,kind:'offering',ifcClass:'IfcPumpType',
      name:'Supplier pump offer',graphPath:'remote/offer',datasetId:'remote',
      sources:[{authorityDid:manufacturer,recordId:'pump',revision:1}],
      dependencies:[{id:'pump',authorityDid:manufacturer,revision:1,kind:'product',ifcClass:'IfcPumpType',
        name:'Pump type',graphPath:'remote/pump',datasetId:'catalog',sources:[],dependencies:[]}]};
    const graph={authorityDid:owner,datasetId:'project',schemas:{},productResolution:{mode:'current-published',
      definitions:[{path:'types/pump',authorityDid:manufacturer,recordId:'pump',revision:1}]},entities:{
      'types/pump':{components:{[identity]:{authorityDid:manufacturer,recordId:'pump',revision:1}}},
      supply:{components:{[schema]:{class:'IfcPumpType',properties:{supplyChain:{
        id:'local',authorityDid:owner,revision:1,kind:'supply',sources:snapshot.sources,
        acceptedFrom:{snapshot}}}}},inherits:{manufacturerType:'types/pump'}}},
      effectiveComponents:{'types/pump':{},supply:{}}};
    state.graphs=[graph];const network=entityNetworkData();
    return {snapshots:network.entries.filter(entry=>entry.snapshot).map(entry=>({
      name:entry.label,authority:entry.graph.authorityDid})),
      received:network.links.filter(link=>link.kind==='Received from').length,
      sourced:network.links.filter(link=>link.kind==='Sourced from').length,
      inventoryCount:inventory().entries.length};
  `);
  assert.deepEqual(result.snapshots, [{ name: "Supplier pump offer", authority: "did:web:s.example" }]);
  assert.equal(result.received, 1);
  assert.equal(result.sourced, 2);
  assert.equal(result.inventoryCount, 2);
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

for (const demoOpenAccess of [true, false]) {
  test(`dashboard refresh updates an already-rendered access icon (demo=${demoOpenAccess})`, async () => {
    const elements = new Map();
    const element = (tagName = "div") => ({
      tagName, dataset: {}, children: [], value: "", textContent: "",
      classList: { toggle() {} },
      setAttribute(name, value) { this[name] = value; },
      replaceChildren(...children) { this.children = children; },
      append(child) { this.children.push(child); },
      querySelector(tag) { return this.children.find((child) => child.tagName === tag) || null; },
      get firstChild() { return this.children[0]; },
      get lastChild() { return this.children.at(-1); },
    });
    const get = (id) => {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    };
    get("access").append(element("svg"));
    get("access-submit").append(element("#text"));
    const graph = { datasetId: "urn:demo" };
    const responses = {
      "/clip/v1/node/info": { did: "did:web:owner.example", role: "owner", demoOpenAccess },
      "/ifc/v1/datasets": { items: [{ datasetId: graph.datasetId }] },
      "/ifc/v1/datasets/urn%3Ademo/graph": graph,
      "/ifc/v1/datasets/urn%3Ademo/graph?refresh_products=true": graph,
      "/ifc/v1/datasets/urn%3Ademo/history": { items: [] },
      "/clip/v1/network/gossip/peers": [],
      "/clip/v1/replication/status": { replicas: [], acknowledgements: [] },
      "/clip/v1/projects": { items: [] },
    };
    const renderedIcons = [];
    let renders = 0;
    let refreshEvents = 0;
    const context = vm.createContext({
      document: { getElementById: get, createElement: element },
      lucide: {
        createIcons() {
          const placeholder = get("access").querySelector("i");
          if (placeholder) {
            renderedIcons.push(placeholder.dataset.lucide);
            get("access").replaceChildren(element("svg"));
          }
        },
      },
      fetch: async (path) => {
        assert.ok(Object.hasOwn(responses, path), `Unexpected request: ${path}`);
        return { ok: true, text: async () => JSON.stringify(responses[path]) };
      },
      window: { dispatchEvent() { refreshEvents++; } },
      CustomEvent: class {},
      recordRender() { renders++; },
    });
    vm.runInContext(script.slice(0, script.indexOf("function activateView")), context);
    vm.runInContext("render = recordRender; function activateView() {}", context);
    for (let iteration = 0; iteration < 2; iteration++) {
      assert.equal(get("access").querySelector("i"), null);
      assert.equal(await vm.runInContext("refresh()", context), true);
      assert.equal(get("refresh").disabled, false);
      assert.equal(get("notice").textContent, "");
      assert.equal(get("authority-label").title, "did:web:owner.example");
      assert.equal(vm.runInContext("state.graphs[0].datasetId", context), graph.datasetId);
    }
    assert.deepEqual(renderedIcons, Array(2).fill(demoOpenAccess ? "hard-drive" : "key-round"));
    assert.equal(renders, 2);
    assert.equal(refreshEvents, 2);
  });
}
