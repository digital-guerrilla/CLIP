"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  info: null,
  catalog: [],
  graphs: [],
  history: [],
  peers: [],
  replicas: [],
  acknowledgements: [],
  scope: "",
  facility: null,
  asset: null,
  allPeers: false,
  mode: "dataset",
  networkDid: null,
  request: 0,
  storage: false,
  authorized: false,
  pendingProject: false,
  pendingInvite: false,
  inviteProject: null,
  latestInviteId: null,
  reviewProject: null,
  projects: [],
  templates: null,
  editingProject: null,
  editingMembers: {},
  lineage: null,
  entityLayouts: new Map(),
};
const colors = {
  import: "#168164",
  contribution: "#b57e2a",
  replica: "#317fa5",
  peer: "#9aa9a1",
};
function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function icon(name) {
  const result = node("i");
  result.dataset.lucide = name;
  return result;
}
function icons() {
  lucide.createIcons();
}
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
  $("notice").classList.toggle("error", error);
}
function empty(parent, message, symbol = "inbox") {
  parent.replaceChildren();
  const block = node("div", undefined, "empty-state");
  block.append(icon(symbol), node("p", message));
  parent.append(block);
}
function detail(title, value) {
  const result = node("details", undefined, "proof-detail");
  result.append(
    node("summary", title),
    node("pre", JSON.stringify(value, null, 2)),
  );
  return result;
}
async function api(path, method = "GET", body) {
  const headers = { Accept: "application/json" };
  if ($("key").value) headers["x-api-key"] = $("key").value;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(
      "Authority returned an invalid response (" + response.status + ")",
    );
  }
  if (!response.ok) {
    const error = new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail || response.status),
    );
    error.status = response.status;
    throw error;
  }
  return value;
}
function readable(value) {
  return String(value || "")
    .replace(/^urn:/, "")
    .split(/[:/_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
function didLabel(did) {
  if (did === state.info?.did)
    return readable(state.info.role || "Local authority");
  try {
    const host = decodeURIComponent(did.replace(/^did:web:/, ""));
    if (/^(127\.0\.0\.1|localhost):\d+$/.test(host))
      return "Node " + host.split(":")[1];
    return host.split(":")[0];
  } catch {
    return did;
  }
}
function dateText(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}
function scopedGraphs() {
  return state.graphs.filter(
    (graph) => !state.scope || graph.datasetId === state.scope,
  );
}
function scopedHistory() {
  return state.history.filter(
    (item) => !state.scope || item.datasetId === state.scope,
  );
}
function entityKey(graph, path) {
  const components = graph.entities?.[path]?.components;
  const workflow = components?.["urn:clip:construction:source:v1"]?.properties?.supplyChain;
  const definition = workflow?.kind === "product" && !workflow.acceptedFrom
    ? graph.productResolution?.definitions?.find((item) => item.authorityDid === workflow.authorityDid && item.recordId === workflow.id &&
      (graph.productResolution.mode !== "pinned" || item.revision === workflow.revision))
    : null;
  const product = components?.["urn:clip:construction:product-identity:v1"] || definition;
  if (product?.authorityDid && product?.recordId)
    return JSON.stringify(["manufacturer-product", product.authorityDid, product.recordId,
      graph.productResolution?.mode === "pinned" ? product.revision : "current"]);
  const identity = components?.["urn:clip:construction:entity-identity:v1"];
  if (identity?.authorityDid && identity?.datasetId && identity?.entityPath)
    return JSON.stringify([identity.authorityDid, identity.datasetId, identity.entityPath]);
  return JSON.stringify([graph.authorityDid, graph.datasetId, path]);
}

function inventoryEntities() {
  const selected = new Map();
  function include(graph, path) {
    const key = JSON.stringify([graph.authorityDid, graph.datasetId, path]);
    if (selected.has(key) || !graph.entities[path]) return;
    selected.set(key, { graph, path });
    const location = graph.entities[path].components?.["urn:clip:construction:source:v1"]?.properties?.locationReference;
    if (location) {
      const model = state.graphs.find((item) => item.authorityDid === location.authorityDid && item.datasetId === location.datasetId);
      if (!model?.entities[location.entityPath])
        throw new Error("Referenced installation location is unavailable: " +
          location.authorityDid + " / " + location.datasetId + " / " + location.entityPath);
      include(model, location.entityPath);
      ancestors(model, location.entityPath);
    }
  }
  function ancestors(graph, path, visited = new Set()) {
    if (visited.has(path)) return;
    visited.add(path);
    for (const [parent, entity] of Object.entries(graph.entities))
      if (Object.values(entity.children || {}).includes(path)) {
        include(graph, parent);
        ancestors(graph, parent, visited);
      }
  }
  for (const graph of scopedGraphs())
    for (const path of Object.keys(graph.entities).sort((a, b) =>
      Number(Boolean(graph.entities[b].components?.["urn:clip:construction:product-identity:v1"])) -
      Number(Boolean(graph.entities[a].components?.["urn:clip:construction:product-identity:v1"])))) include(graph, path);
  return [...selected.values()];
}
function entityLabel(graph, path) {
  const component = graph.effectiveComponents[path] || {};
  const own = graph.entities[path]?.components || {};
  if (!own["ifc::name"] && component["ifc::serial"])
    return (component["ifc::name"] || readable(path.split("/").pop())) + " / " + component["ifc::serial"];
  return typeof component["ifc::name"] === "string"
    ? component["ifc::name"]
    : readable(path.split("/").pop());
}
function entityAuthority(entry) {
  const components = entry.graph.entities[entry.path].components;
  return components?.["urn:clip:construction:product-identity:v1"]?.authorityDid ||
    components?.["urn:clip:construction:entity-identity:v1"]?.authorityDid ||
    entry.graph.authorityDid;
}
function entityOwnerLabel(entry) {
  const authority = entityAuthority(entry);
  const product = entry.graph.entities[entry.path].components?.["urn:clip:construction:product-identity:v1"];
  const manufacturer = entry.graph.effectiveComponents[entry.path]?.["urn:clip:construction:manufacturer-data:v1"]?.data?.manufacturer;
  return "Data owner: " + (product && typeof manufacturer === "string" && manufacturer.trim()
    ? manufacturer + " / " + didLabel(authority) : didLabel(authority));
}
function entityKind(graph, path) {
  const entity = graph.entities[path],
    components = graph.effectiveComponents[path] || {};
  const source = components["urn:clip:construction:source:v1"];
  const className = source?.class || "";
  const workflow = source?.properties?.supplyChain;
  if (workflow && (["offering", "supply"].includes(workflow.kind) ||
      workflow.kind === "product" && workflow.acceptedFrom)) return "event";
  if (["IfcZone", "IfcGroup"].includes(className) || components["urn:clip:construction:product-library:v1"])
    return "container";
  if (/^types?$/i.test(path) && Object.keys(entity.children || {}).length) return "container";
  if (/^(types?|events?)(\/|$)/i.test(path))
    return /^types?/i.test(path) ? "type" : "event";
  if (className === "Type" || /^Ifc.*Type$/.test(className)) return "type";
  if (Object.keys(components).some((key) => /event(?:$|:v\d+$)/.test(key)))
    return "event";
  const leaf = path.split("/").pop();
  if (
    /Ifc(Project|Site|Building|BuildingStorey|Space|Facility|FacilityPart)$/i.test(
      className,
    ) ||
    /^(building|facility|site|floor|storey|space)([-\d]|$)/i.test(leaf)
  )
    return "facility";
  if (Object.keys(entity.children || {}).length) return "container";
  return "asset";
}
function symbolFor(entry) {
  const source =
    entry.graph.effectiveComponents[entry.path]?.[
      "urn:clip:construction:source:v1"
    ];
  if (entry.kind === "facility" || entry.kind === "container")
    return "building-2";
  if (entry.kind === "event") return "clipboard-check";
  if (entry.kind === "type") return "component";
  if (/door/i.test(entry.path + " " + source?.class)) return "door-open";
  if (/window/i.test(entry.path + " " + source?.class))
    return "panels-top-left";
  if (/pump|hvac|fan/i.test(entry.path + " " + source?.class)) return "fan";
  return "box";
}
function inventory() {
  const entries = [],
    lookup = new Map();
  const contributions = inventoryEntities();
  for (const { graph, path } of contributions) {
      const entry = {
        graph,
        path,
        key: entityKey(graph, path),
        label: entityLabel(graph, path),
        kind: entityKind(graph, path),
        parent: null,
      };
      if (lookup.has(entry.key)) {
        lookup.get(entry.key).contributions.push({ graph, path });
        continue;
      }
      entry.contributions = [{ graph, path }];
      entries.push(entry);
      lookup.set(entry.key, entry);
    }
  for (const { graph, path } of contributions) {
    const entry = lookup.get(entityKey(graph, path));
    for (const target of Object.values(
      graph.entities[path].children || {},
    )) {
      const child = lookup.get(entityKey(graph, target));
      if (child && !child.parent && child !== entry) child.parent = entry;
    }
    const reference =
      graph.effectiveComponents[path]?.[
        "urn:clip:construction:reference:v1"
      ];
    if (typeof reference === "string") {
      const parent = lookup.get(entityKey(graph, reference));
      if (
        parent &&
        (parent.kind === "facility" || parent.kind === "container") &&
        parent !== entry
      )
        entry.parent = parent;
    }
    const location = graph.entities[path].components?.["urn:clip:construction:source:v1"]?.properties?.locationReference;
    if (location) {
      const model = state.graphs.find((item) => item.authorityDid === location.authorityDid && item.datasetId === location.datasetId);
      const parent = model && lookup.get(entityKey(model, location.entityPath));
      if (parent && parent !== entry) entry.parent = parent;
    }
  }
  for (const entry of entries)
    if (entry.kind === "asset" && entries.some((child) => child.parent === entry)) entry.kind = "container";
  return { entries, lookup };
}
function descendants(entry, entries) {
  const result = [],
    visited = new Set([entry.key]),
    pending = [entry];
  while (pending.length) {
    const current = pending.shift();
    for (const child of entries.filter(
      (candidate) => candidate.parent === current,
    )) {
      if (!visited.has(child.key)) {
        visited.add(child.key);
        result.push(child);
        pending.push(child);
      }
    }
  }
  return result;
}
function origins() {
  const result = new Map();
  function add(did, type, value) {
    if (!did || did === state.info?.did) return;
    if (!result.has(did))
      result.set(did, {
        did,
        types: new Set(),
        imports: [],
        contributions: [],
        replicas: [],
        peer: null,
      });
    const origin = result.get(did);
    origin.types.add(type);
    if (type === "import") origin.imports.push(value);
    if (type === "contribution") origin.contributions.push(value);
    if (type === "replica") origin.replicas.push(value);
    if (type === "peer") origin.peer = value;
  }
  for (const graph of scopedGraphs())
    for (const source of graph.sources || [])
      add(source.publisherDid, "import", source);
  for (const item of scopedHistory())
    add(item.transaction.proposal?.actorDid, "contribution", item);
  for (const ack of state.acknowledgements)
    if (!state.scope || ack.payload?.datasetId === state.scope)
      add(ack.actorDid, "replica", ack);
  if (state.allPeers)
    for (const peer of state.peers) add(peer.did, "peer", peer);
  return [...result.values()];
}
function originType(origin) {
  return ["import", "contribution", "replica", "peer"].find((type) =>
    origin.types.has(type),
  );
}
function originSummary(origin) {
  const parts = [];
  if (origin.imports.length)
    parts.push(
      origin.imports.length +
        " verified import" +
        (origin.imports.length === 1 ? "" : "s"),
    );
  if (origin.contributions.length)
    parts.push(
      origin.contributions.length +
        " accepted contribution" +
        (origin.contributions.length === 1 ? "" : "s"),
    );
  if (origin.replicas.length) parts.push("Replica acknowledged");
  if (!parts.length) parts.push(origin.peer?.status || "Known peer");
  return parts.join(" / ");
}
let networkZoom;
function renderNetwork() {
  const sources = origins();
  const canvas = d3.select("#network");
  const compact = matchMedia("(max-width:760px)").matches;
  const mapWidth = compact ? 480 : 900;
  const centerX = mapWidth / 2;
  canvas.attr("viewBox", `0 0 ${mapWidth} 430`);
  canvas.selectAll("*").remove();
  const defs = canvas.append("defs");
  for (const [kind, color] of Object.entries(colors))
    defs
      .append("marker")
      .attr("id", "arrow-" + kind)
      .attr("viewBox", "0 -4 8 8")
      .attr("refX", 7)
      .attr("markerWidth", 7)
      .attr("markerHeight", 7)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-4L8,0L0,4")
      .attr("fill", color);
  const group = canvas.append("g");
  networkZoom = d3
    .zoom()
    .scaleExtent([0.45, 2.5])
    .on("zoom", (event) => group.attr("transform", event.transform));
  canvas.call(networkZoom).on("dblclick.zoom", null);
  const local = {
    id: state.info?.did || "local",
    local: true,
    x: centerX,
    y: 210,
    fx: centerX,
    fy: 210,
  };
  const nodes = [
    local,
    ...sources.map((origin, index) => {
      const angle =
        (index / Math.max(sources.length, 1)) * Math.PI * 2 - Math.PI * 0.8;
      return {
        id: origin.did,
        origin,
        x: centerX + Math.cos(angle) * (compact ? 175 : 285),
        y: 210 + Math.sin(angle) * 142,
      };
    }),
  ];
  const edges = [];
  for (const remote of nodes.slice(1))
    for (const type of remote.origin.types)
      edges.push({
        source: type === "replica" ? local : remote,
        target: type === "replica" ? remote : local,
        type,
      });
  if (nodes.length > 2) {
    const simulation = d3
      .forceSimulation(nodes)
      .randomSource(d3.randomLcg(0.42))
      .force("charge", d3.forceManyBody().strength(-450))
      .force("collision", d3.forceCollide(75))
      .force("x", d3.forceX(centerX).strength(0.013))
      .force("y", d3.forceY(210).strength(0.025))
      .stop();
    for (let tick = 0; tick < 100; tick++) simulation.tick();
    for (const remote of nodes.slice(1)) {
      remote.x = Math.max(compact ? 65 : 90, Math.min(mapWidth - (compact ? 65 : 90), remote.x));
      remote.y = Math.max(73, Math.min(300, remote.y));
    }
  }
  const edgeGeometry = (edge) => {
    const dx = edge.target.x - edge.source.x,
      dy = edge.target.y - edge.source.y,
      length = Math.hypot(dx, dy) || 1;
    const types = edge.source.origin?.types || edge.target.origin?.types || [];
    const offset = ([...types].indexOf(edge.type) - 0.5) * 18;
    const sx = edge.source.x + (dx / length) * (edge.source.local ? 49 : 37),
      sy = edge.source.y + (dy / length) * (edge.source.local ? 49 : 37),
      tx = edge.target.x - (dx / length) * (edge.target.local ? 54 : 43),
      ty = edge.target.y - (dy / length) * (edge.target.local ? 54 : 43);
    return {
      path: `M${sx},${sy} Q${(sx + tx) / 2 - (dy / length) * offset},${(sy + ty) / 2 + (dx / length) * offset} ${tx},${ty}`,
      x: (sx + tx) / 2 - (dy / length) * offset + (dx / length) * (compact ? 12 : 60),
      y: (sy + ty) / 2 + (dx / length) * offset,
    };
  };
  group
    .selectAll(".network-edge")
    .data(edges)
    .join("path")
    .attr("class", "network-edge")
    .attr("d", (edge) => edgeGeometry(edge).path)
    .attr("fill", "none")
    .attr("stroke", (edge) => colors[edge.type])
    .attr("stroke-width", (edge) => (edge.type === "peer" ? 1 : 1.7))
    .attr("stroke-dasharray", (edge) => (edge.type === "peer" ? "5 6" : null))
    .attr("marker-end", (edge) =>
      edge.type === "peer" ? null : "url(#arrow-" + edge.type + ")",
    );
  group
    .selectAll(".network-link-label")
    .data(edges.filter((edge) => edge.type !== "peer"))
    .join("text")
    .attr("class", "network-link-label")
    .attr("text-anchor", "middle")
    .attr("x", (edge) => edgeGeometry(edge).x)
    .attr("y", (edge) => edgeGeometry(edge).y - 8)
    .text(
      (edge) =>
        ({
          import: "Imported data",
          contribution: "Accepted proposal",
          replica: "Replica placement",
        })[edge.type],
    );
  const buttons = group
    .selectAll(".network-node")
    .data(nodes)
    .join("g")
    .attr("class", "network-node")
    .attr("transform", (item) => `translate(${item.x},${item.y})`)
    .attr("tabindex", 0)
    .attr("role", "button")
    .attr(
      "aria-label",
      (item) =>
        didLabel(item.id) +
        (item.local
          ? " / local authority"
          : " / " + originSummary(item.origin)),
    )
    .on("click", (_, item) => selectOrigin(item.id))
    .on("keydown", (event, item) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectOrigin(item.id);
      }
    });
  buttons
    .append("circle")
    .attr("r", (item) => (item.local ? 54 : 42))
    .attr("fill", (item) => (item.local ? "#e7f2eb" : "#fff"))
    .attr("stroke", (item) => (item.local ? "#c5dfce" : "#e5ece7"))
    .attr("stroke-width", 1);
  buttons
    .append("circle")
    .attr("class", "node-disc")
    .attr("r", (item) => (item.local ? 44 : 33))
    .attr("fill", (item) => (item.local ? colors.import : "#fff"))
    .attr("stroke", (item) =>
      item.local ? colors.import : colors[originType(item.origin)],
    )
    .attr("stroke-width", 1.5);
  buttons.each(function (item) {
    const glyph = lucide.createElement(
      item.local
        ? lucide.Building2
        : item.origin.types.has("import")
          ? lucide.Factory
          : item.origin.types.has("contribution")
            ? lucide.HardHat
            : item.origin.types.has("replica")
              ? lucide.Database
              : lucide.Server,
    );
    glyph.setAttribute("x", "-13");
    glyph.setAttribute("y", "-13");
    glyph.setAttribute("width", "26");
    glyph.setAttribute("height", "26");
    glyph.setAttribute(
      "stroke",
      item.local ? "white" : colors[originType(item.origin)],
    );
    this.append(glyph);
  });
  buttons
    .append("text")
    .attr("class", "node-title")
    .attr("text-anchor", "middle")
    .attr("y", (item) => (item.local ? 73 : 62))
    .text((item) => {
      const label = didLabel(item.id);
      return label.length > 27 ? label.slice(0, 24) + "..." : label;
    });
  buttons
    .append("text")
    .attr("class", "node-subtitle")
    .attr("text-anchor", "middle")
    .attr("y", (item) => (item.local ? 89 : 78))
    .text((item) =>
      item.local
        ? "Local authority"
        : item.origin.peer?.status === "dead"
          ? "Peer offline"
          : item.origin.imports.length
            ? "Verified publisher"
            : item.origin.contributions.length
              ? "Proposal author"
              : item.origin.replicas.length
                ? "Replica placement"
                : "Gossip peer",
    );
  buttons.append("title").text((item) => item.id);
  const occupied = [];
  buttons.selectAll("text,circle").each(function () {
    const box = this.getBBox();
    const position = d3.select(this.parentNode).datum();
    occupied.push({ x: box.x + position.x, y: box.y + position.y, width: box.width, height: box.height });
  });
  group.selectAll(".network-link-label").each(function () {
    const initialX = Number(this.getAttribute("x"));
    const initialY = Number(this.getAttribute("y"));
    for (const [offsetX, offsetY] of [[0, 0], [-30, 0], [30, 0], [0, -22], [0, 22], [-60, 0], [60, 0], [-30, -24], [30, -24]]) {
      this.setAttribute("x", initialX + offsetX);
      this.setAttribute("y", initialY + offsetY);
      const box = this.getBBox();
      if (!occupied.some(other => box.x < other.x + other.width + 4 && box.x + box.width + 4 > other.x && box.y < other.y + other.height + 4 && box.y + box.height + 4 > other.y)) {
        occupied.push(box);
        break;
      }
    }
  });
  $("network-empty").hidden = sources.length > 0;
  $("origin-count").textContent = sources.length;
  document.querySelector(".source-panel-heading .eyebrow").textContent = state.allPeers ? "AUTHORITIES" : "DATA ORIGINS";
  $("peer-auth").hidden = state.authorized;
  $("source-list").replaceChildren();
  if (!sources.length)
    empty(
      $("source-list"),
      "No imported data or accepted external contributions.",
      "unplug",
    );
  for (const origin of sources) {
    const type = originType(origin);
    const row = node(
      "button",
      undefined,
      "source-row" + (state.networkDid === origin.did ? " selected" : ""),
    );
    row.dataset.did = origin.did;
    const symbol = node(
      "span",
      undefined,
      "source-symbol " +
        {
          import: "green",
          contribution: "amber",
          replica: "blue",
          peer: "neutral",
        }[type],
    );
    symbol.append(
      icon(
        type === "import"
          ? "factory"
          : type === "contribution"
            ? "hard-hat"
            : type === "replica"
              ? "database"
              : "server",
      ),
    );
    const body = node("span", undefined, "source-text");
    body.append(
      node("strong", didLabel(origin.did)),
      node("small", originSummary(origin)),
    );
    row.append(symbol, body, icon("chevron-right"));
    row.onclick = () => selectOrigin(origin.did);
    $("source-list").append(row);
  }
  if (state.networkDid) selectOrigin(state.networkDid);
  else $("node-detail").hidden = true;
  icons();
}
function selectOrigin(did) {
  state.networkDid = did;
  const panel = $("node-detail");
  panel.hidden = false;
  panel.replaceChildren(node("h3", didLabel(did)), node("p", did));
  document
    .querySelectorAll(".source-row")
    .forEach((row) =>
      row.classList.toggle("selected", row.dataset.did === did),
    );
  if (did === state.info?.did) {
    panel.append(
      node("span", "Local authority", "badge"),
      node("p", state.catalog.length + " registered datasets"),
    );
    return;
  }
  const origin = origins().find((item) => item.did === did);
  if (!origin) {
    panel.hidden = true;
    return;
  }
  for (const source of origin.imports) {
    panel.append(
      node("span", "Verified import", "badge"),
      node("p", readable(source.datasetId)),
      node("p", source.entityPaths.length + " contributed entities"),
    );
    panel.append(detail("Publisher & byte pin", source));
  }
  for (const contribution of origin.contributions.slice(-3)) {
    panel.append(
      node("span", "Owner accepted", "badge amber"),
      node("p", contribution.transaction.proposal?.target?.entityPath || ""),
      node("p", "Sequence " + contribution.receipt.sequence),
    );
  }
  for (const receipt of origin.replicas) {
    panel.append(node("span", "Replica acknowledged", "badge blue"), node("p", receipt.payload?.datasetId || ""), detail("Signed acknowledgement", receipt));
  }
  if (origin.peer)
    panel.append(
      node(
        "p",
        "Membership: " +
          origin.peer.status +
          " / generation " +
          origin.peer.generation,
      ),
    );
}
function renderPortfolio() {
  const { entries } = inventory();
  const facilities = entries.filter((entry) =>
    ["facility", "container"].includes(entry.kind),
  );
  const assets = entries.filter((entry) => entry.kind === "asset");
  $("facility-count").textContent = facilities.length;
  $("asset-count").textContent = assets.length;
  $("source-count").textContent = origins().filter(
    (origin) => origin.imports.length || origin.contributions.length,
  ).length;
  $("transaction-count").textContent = scopedHistory().length;
  $("portfolio-summary").textContent =
    facilities.length + " containers / " + assets.length + " asset occurrences";
  $("facility-portfolio").replaceChildren();
  if (!facilities.length)
    empty(
      $("facility-portfolio"),
      "No facility containment is recorded in this dataset.",
      "building-2",
    );
  for (const facility of facilities) {
    const children = descendants(facility, entries).filter(
      (entry) => entry.kind === "asset",
    );
    const sourceDids = new Set(
      children.flatMap((entry) =>
        entitySources(entry).map((source) => source.did),
      ),
    );
    const card = node("article", undefined, "facility-card");
    const band = node("div", undefined, "facility-band"),
      symbol = node("span", undefined, "facility-symbol"),
      title = node("div");
    symbol.append(icon("building-2"));
    title.append(
      node("h3", facility.label),
      node("small", facility.graph.datasetId),
    );
    band.append(symbol, title);
    const counts = node("div", undefined, "facility-data");
    for (const [count, label] of [
      [children.length, "Assets"],
      [sourceDids.size, "Data sources"],
    ]) {
      const stat = node("div");
      stat.append(node("strong", count), node("span", label));
      counts.append(stat);
    }
    const preview = node("div", undefined, "facility-assets");
    children.slice(0, 2).forEach((entry) => {
      const row = node("div", undefined, "facility-asset");
      row.append(icon(symbolFor(entry)), node("span", entry.label));
      preview.append(row);
    });
    const footer = node("div", undefined, "facility-footer");
    footer.append(node("small", facility.path));
    const open = node("button", "Explore", "text-button");
    open.append(icon("arrow-right"));
    open.onclick = () => {
      state.facility = facility.key;
      state.asset = null;
      $("asset-search").value = "";
      activateView("assets");
      renderAssets();
    };
    footer.append(open);
    card.append(band, counts, preview, footer);
    $("facility-portfolio").append(card);
  }
  icons();
}
function resolvePath(graph, path) {
  if (graph.entities[path]) return path;
  const parts = path.split("/");
  let current = parts.shift();
  for (const part of parts) current = graph.entities[current]?.children?.[part];
  return graph.entities[current] ? current : path;
}
function inheritedPaths(entry) {
  const paths = new Set(),
    pending = [entry.path];
  while (pending.length) {
    const path = resolvePath(entry.graph, pending.pop());
    if (paths.has(path)) continue;
    paths.add(path);
    const entity = entry.graph.entities[path];
    if (entity) pending.push(...Object.values(entity.inherits || {}));
    for (const [key, value] of Object.entries(
      entry.graph.effectiveComponents[path] || {},
    ))
      if (
        entry.graph.schemas[key]?.value?.dataType === "Reference" &&
        typeof value === "string"
      )
        pending.push(value);
  }
  return paths;
}
function proposalTouchesPaths(proposal, paths) {
  return paths.has(proposal?.target?.entityPath) || (proposal?.operations || []).some((operation) => paths.has(operation.node?.path));
}
function entitySources(entry) {
  if (entry.contributions?.length > 1) {
    const sources = entry.contributions.flatMap(({ graph, path }) =>
      entitySources({ ...entry, graph, path, contributions: [] }));
    return [...new Map(sources.map((source) => [source.did, source])).values()];
  }
  const paths = inheritedPaths(entry),
    sources = [];
  for (const source of entry.graph.sources || [])
    if (source.entityPaths.some((path) => paths.has(path)))
      sources.push({
        did: source.publisherDid,
        label: readable(source.datasetId),
        kind: "import",
      });
  for (const product of entry.graph.productResolution?.definitions || [])
    if (paths.has(product.path))
      sources.push({ did: product.authorityDid, label: "Shared manufacturer product / revision " + product.revision,
        kind: "import" });
  for (const item of state.history)
    if (
      item.datasetId === entry.graph.datasetId &&
      proposalTouchesPaths(item.transaction.proposal, paths)
    )
      sources.push({
        did: item.transaction.proposal.actorDid,
        label: "Accepted contribution",
        kind: "contribution",
      });
  return [...new Map(sources.map((source) => [source.did, source])).values()];
}
function activityRow(item, proof = false) {
  const proposal = item.transaction.proposal || {},
    target = proposal.target || {};
  const row = node("article", undefined, "activity-row"),
    symbol = node("span", undefined, "activity-marker");
  symbol.append(icon("check"));
  const body = node("div", undefined, "activity-body");
  body.append(
    node("strong", readable(target.componentSchemaId || item.transaction.kind)),
  );
  const meta = node("div", undefined, "activity-meta");
  meta.append(
    node("span", target.entityPath || ""),
    node("span", didLabel(proposal.actorDid || "")),
    node("span", "Sequence " + item.receipt.sequence),
  );
  body.append(meta);
  if (proof)
    body.append(detail("Signed transaction & authority receipt", item));
  const time = node("time", dateText(proposal.created));
  if (proposal.created) time.dateTime = proposal.created;
  row.append(symbol, body, time);
  return row;
}
function renderActivity() {
  const items = [...scopedHistory()].sort(
    (first, second) =>
      new Date(second.transaction.proposal?.created || 0) -
      new Date(first.transaction.proposal?.created || 0),
  );
  for (const [id, limit, proof] of [
    ["recent-activity", 4, false],
    ["activity-list", Infinity, true],
  ]) {
    const parent = $(id);
    parent.replaceChildren();
    if (!items.length) empty(parent, "No accepted transactions.", "activity");
    for (const item of items.slice(0, limit))
      parent.append(activityRow(item, proof));
  }
  icons();
}
function renderAssets() {
  const { entries, lookup } = inventory(),
    facilities = entries.filter((entry) =>
      ["facility", "container"].includes(entry.kind),
    );
  if (
    state.facility &&
    state.facility !== "unassigned" &&
    !lookup.has(state.facility)
  )
    state.facility = null;
  const facility = lookup.get(state.facility);
  const eligible =
    state.facility === "unassigned"
      ? entries.filter((entry) => !entry.parent)
      : facility
        ? descendants(facility, entries)
        : entries;
  const search = $("asset-search").value.toLowerCase().trim();
  const results = eligible.filter(
    (entry) =>
      entry.kind === "asset" &&
      (!search ||
        (
          entry.label +
          " " +
          entry.path +
          " " +
          JSON.stringify(entry.graph.effectiveComponents[entry.path])
        )
          .toLowerCase()
          .includes(search)),
  );
  $("tree-count").textContent = facilities.length;
  $("asset-tree").replaceChildren();
  function treeButton(label, key, symbol, count) {
    const button = node(
      "button",
      undefined,
      "tree-button" + (state.facility === key ? " selected" : ""),
    );
    button.append(icon(symbol), node("span", label), node("small", count));
    button.onclick = () => {
      state.facility = key;
      state.asset = key === "unassigned" ? null : key;
      renderAssets();
      renderEntityNetwork();
    };
    return button;
  }
  $("asset-tree").append(
    treeButton(
      "All assets",
      null,
      "boxes",
      entries.filter((entry) => entry.kind === "asset").length,
    ),
  );
  const visited = new Set();
  function branch(entry) {
    visited.add(entry.key);
    const wrapper = node("div");
    wrapper.append(
      treeButton(
        entry.label,
        entry.key,
        "building-2",
        descendants(entry, entries).filter((child) => child.kind === "asset")
          .length,
      ),
    );
    const children = facilities.filter(
      (child) => child.parent === entry && !visited.has(child.key),
    );
    if (children.length) {
      const nested = node("div", undefined, "tree-children");
      children.forEach((child) => nested.append(branch(child)));
      wrapper.append(nested);
    }
    return wrapper;
  }
  for (const entry of facilities.filter(
    (entry) => !entry.parent || !facilities.includes(entry.parent),
  ))
    $("asset-tree").append(branch(entry));
  for (const entry of facilities.filter((entry) => !visited.has(entry.key)))
    $("asset-tree").append(branch(entry));
  const unassigned = entries.filter(
    (entry) => entry.kind === "asset" && !entry.parent,
  );
  if (unassigned.length)
    $("asset-tree").append(
      treeButton("Unassigned assets", "unassigned", "box", unassigned.length),
    );
  $("asset-results-title").textContent =
    state.facility === "unassigned"
      ? "Unassigned assets"
      : facility?.label || "All assets";
  $("asset-results-count").textContent = results.length + " assets";
  $("asset-list").replaceChildren();
  if (!results.length)
    empty(
      $("asset-list"),
      search
        ? "No matching assets."
        : "No asset occurrences in this selection.",
      "search",
    );
  for (const entry of results) {
    const button = node(
        "button",
        undefined,
        "asset-row" + (state.asset === entry.key ? " selected" : ""),
      ),
      symbol = node("span", undefined, "asset-symbol"),
      body = node("span", undefined, "asset-row-text");
    symbol.append(icon(symbolFor(entry)));
    body.append(
      node("strong", entry.label),
      node(
        "small",
        (entry.parent ? entry.parent.label + " / " : "") + entry.path,
      ),
    );
    button.append(symbol, body);
    const status = assetStatus(entry);
    if (status) button.append(node("span", status, "badge"));
    button.append(icon("chevron-right"));
    button.onclick = () => {
      state.asset = entry.key;
      renderAssets();
      renderEntityNetwork();
    };
    $("asset-list").append(button);
  }
  renderInspector(lookup.get(state.asset) || entityNetworkData().entries.find((entry) => entry.key === state.asset));
  icons();
}
function assetStatus(entry) {
  const components = entry.graph.effectiveComponents[entry.path] || {};
  const workflow = components["urn:clip:construction:source:v1"]?.properties?.supplyChain;
  if (["installation", "asset"].includes(workflow?.kind) && workflow.data?.status)
    return readable(workflow.data.status);
  for (const [key, value] of Object.entries(components)) {
    if (value && typeof value === "object" && value.status)
      return readable(value.status);
    if (typeof value === "string" && /reference/.test(key)) {
      const event = entry.graph.effectiveComponents[value];
      if (event)
        for (const component of Object.values(event))
          if (component && typeof component === "object" && component.status)
            return readable(component.status);
    }
  }
  return "";
}
function entityNetworkData() {
  const { entries } = inventory();
  const links = [];
  const known = new Map(entries.map((entry) => [entry.key, entry]));
  const seen = new Set();
  const pins = new Map();
  const pinKey = (value) => JSON.stringify([value.authorityDid, value.recordId || value.id, value.revision]);
  const workflowOf = (entry) => entry.graph.entities[entry.path].components?.["urn:clip:construction:source:v1"]?.properties?.supplyChain;
  for (const entry of entries) {
    const workflow = workflowOf(entry);
    if (workflow) pins.set(pinKey(workflow), entry.key);
  }
  const snapshots = [];
  for (const entry of entries) {
    const original = workflowOf(entry)?.acceptedFrom?.snapshot;
    if (original) snapshots.push({ value: original, graph: entry.graph });
  }
  const visited = new Set();
  while (snapshots.length) {
    const { value, graph } = snapshots.pop();
    const pin = pinKey(value);
    if (visited.has(pin)) continue;
    visited.add(pin);
    for (const dependency of value.dependencies || []) snapshots.push({ value: dependency, graph });
    if (value.acceptedFrom?.snapshot) snapshots.push({ value: value.acceptedFrom.snapshot, graph });
    if (pins.has(pin)) continue;
    const definition = value.kind === "product" && !value.acceptedFrom
      ? graph.productResolution?.definitions?.find((item) => item.authorityDid === value.authorityDid &&
        item.recordId === value.id && (graph.productResolution.mode !== "pinned" || item.revision === value.revision))
      : null;
    if (definition) {
      pins.set(pin, entityKey(graph, definition.path));
      continue;
    }
    const key = JSON.stringify(["source-snapshot", value.authorityDid, value.id, value.revision]);
    const path = value.graphPath;
    const components = { "ifc::name": value.name, "urn:clip:construction:source:v1": {
      format: "CLIP", id: value.id, class: value.ifcClass, properties: { supplyChain: value },
    }};
    const snapshotGraph = { authorityDid: value.authorityDid, datasetId: value.datasetId,
      entities: { [path]: { components, children: {}, inherits: {} } },
      effectiveComponents: { [path]: components }, schemas: graph.schemas, sources: [] };
    const entry = { graph: snapshotGraph, path, key, label: value.name, kind: value.kind === "product" ? "type" : "event",
      parent: null, snapshot: true };
    entries.push(entry);
    known.set(key, entry);
    pins.set(pin, key);
  }
  function add(source, target, kind, label = kind) {
    const key = JSON.stringify([source, target, kind, label]);
    if (source !== target && known.has(source) && known.has(target) && !seen.has(key)) {
      seen.add(key);
      links.push({ source, target, kind, label });
    }
  }
  for (const { graph, path } of inventoryEntities()) {
      const entity = graph.entities[path];
      const source = entityKey(graph, path);
      const workflow = entity.components?.["urn:clip:construction:source:v1"]?.properties?.supplyChain;
      const targetKey = (target) => entityKey(graph, resolvePath(graph, target));
      for (const target of Object.values(entity.children || {}))
        add(source, targetKey(target), known.get(targetKey(target))?.kind === "type" ? "Lists type" : "Contains");
      for (const target of Object.values(entity.inherits || {})) {
        const key = targetKey(target);
        if (known.get(key)?.kind !== "type") {
          add(source, key, workflow?.kind === "installation" ? "Allocated from" : "Inherits");
          continue;
        }
        if (workflow && ["supply", "offering"].includes(workflow.kind)) {
          const manufacturer = graph.entities[resolvePath(graph, target)]?.components?.["urn:clip:construction:product-identity:v1"]?.authorityDid;
          const route = [...new Set((workflow.lineage || [])
            .filter((item) => item.kind === "offering" && item.authorityDid !== manufacturer)
            .map((item) => item.authorityDid))];
          add(source, key, "Supply", route.length ? "Supply via " + route.map(didLabel).join(" / ") : "Direct supply");
        } else add(source, key, workflow?.kind === "product" && workflow.acceptedFrom ? "Product reference" : "Type");
      }
      for (const target of entity.components?.["urn:clip:construction:component-types:v1"] || [])
        add(source, targetKey(target), "Component type");
      const location = entity.components?.["urn:clip:construction:source:v1"]?.properties?.locationReference;
      if (location) {
        const model = state.graphs.find((item) => item.authorityDid === location.authorityDid && item.datasetId === location.datasetId);
        if (model) add(entityKey(model, location.entityPath), source, "Contains");
      }
      for (const [schema, value] of Object.entries(entity.components || {})) {
        if (typeof value === "string" && /installation-reference/.test(schema))
          add(source, targetKey(value), "Event");
        if (value && typeof value === "object" && typeof value.subject === "string" && /event(?:$|:v\d+$)/.test(schema))
          add(targetKey(value.subject), source, "Event");
      }
  }
  for (const entry of entries) {
    const workflow = workflowOf(entry);
    for (const pin of workflow?.sources || []) {
      const target = pins.get(pinKey(pin));
      if (target) add(entry.key, target, workflow.kind === "installation" ? "Allocated from" : "Sourced from");
      else {
        const sourceGraph = scopedGraphs().find((item) => item.authorityDid === pin.authorityDid && item.datasetId === pin.datasetId);
        if (sourceGraph && pin.entityPath)
          add(entry.key, entityKey(sourceGraph, pin.entityPath), workflow.kind === "installation" ? "Allocated from" : "Sourced from");
      }
    }
    const original = workflow?.acceptedFrom?.snapshot;
    if (original) add(entry.key, pins.get(pinKey(original)), "Received from");
  }
  return { entries, links };
}

function entityFlowLink(link) {
  // IFC relationships are dependency-oriented; the visual diagram shows contribution flow.
  return ["Contains", "Lists type"].includes(link.kind) ? link :
    { ...link, source: link.target, target: link.source };
}

function lineageNetworkData(network, rootKey) {
  const root = network.entries.find((entry) => entry.key === rootKey);
  if (!root) return { entries: [], links: [], depths: new Map() };
  const selected = new Set([rootKey]), depths = new Map([[rootKey, 0]]), pending = [rootKey];
  if (root.kind === "type")
    for (const link of network.links.filter((item) => item.target === rootKey && item.kind === "Type")) {
      selected.add(link.source);
      depths.set(link.source, 1);
      pending.push(link.source);
    }
  const traversed = new Set();
  while (pending.length) {
    const current = pending.shift();
    if (traversed.has(current)) continue;
    traversed.add(current);
    for (const link of network.links.filter((item) => item.source === current && !["Contains", "Lists type"].includes(item.kind))) {
      if (!selected.has(link.target)) {
        selected.add(link.target);
        depths.set(link.target, depths.get(current) - 1);
        pending.push(link.target);
      }
    }
  }
  // Add location ancestors only after sourcing traversal, never sibling assets.
  const locations = [...selected];
  while (locations.length) {
    const current = locations.shift();
    for (const link of network.links.filter((item) => item.target === current && item.kind === "Contains")) {
      if (!selected.has(link.source)) {
        selected.add(link.source);
        depths.set(link.source, depths.get(current) - 1);
        locations.push(link.source);
      }
    }
  }
  return { entries: network.entries.filter((entry) => selected.has(entry.key)),
    links: network.links.filter((link) => selected.has(link.source) && selected.has(link.target) && link.kind !== "Lists type"),
    depths };
}

function entityLayoutKey() {
  return JSON.stringify([state.scope, state.lineage]);
}
function entityLayout() {
  const key = entityLayoutKey();
  if (!state.entityLayouts.has(key)) state.entityLayouts.set(key, { positions: new Map(), transform: null });
  return state.entityLayouts.get(key);
}
function moveEntity(key, x, y) {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  entityLayout().positions.set(key, { x, y });
}
function showLineage(key) {
  state.lineage = key;
  state.asset = key;
  renderAssets();
  renderEntityNetwork();
  fitEntityNetwork();
  d3.select("#entity-network").selectAll(".entity-node").filter((entry) => entry.key === key).node()?.focus();
}
function fitEntityNetwork() {
  if (!entityZoom) return;
  const canvas = d3.select("#entity-network");
  const bounds = canvas.select("g").node()?.getBBox();
  if (!bounds?.width || !bounds.height) return;
  const { width, height } = canvas.node().viewBox.baseVal;
  const scale = Math.max(0.1, Math.min(1, (width - 60) / bounds.width, (height - 60) / bounds.height));
  canvas.call(entityZoom.transform, d3.zoomIdentity.translate((width - bounds.width * scale) / 2 - bounds.x * scale,
    (height - bounds.height * scale) / 2 - bounds.y * scale).scale(scale));
}

let entityZoom;
function renderEntityNetwork() {
  const network = entityNetworkData();
  if (state.lineage && !network.entries.some((entry) => entry.key === state.lineage)) state.lineage = null;
  const { entries, links, depths } = state.lineage ? lineageNetworkData(network, state.lineage) : network;
  const layout = entityLayout();
  const hadTransform = Boolean(layout.transform);
  const lineageRoot = network.entries.find((entry) => entry.key === state.lineage);
  $("entity-lineage-status").textContent = lineageRoot ? "Lineage of " + lineageRoot.label +
    (lineageRoot.kind === "type" ? " / installations, sources and component products" : " / upstream sources and location only") : "";
  $("show-all-entities").hidden = !state.lineage;
  $("show-lineage").disabled = !network.entries.some((entry) => entry.key === state.asset);
  const canvas = d3.select("#entity-network");
  canvas.selectAll("*").remove();
  $("entity-network-count").textContent = entries.length + " entities / " +
    entries.filter((entry) => entry.kind === "type").length + " types / " +
    entries.filter((entry) => entry.kind === "asset").length + " assets";
  if (!entries.length) {
    canvas.attr("viewBox", "0 0 900 300").append("text").attr("x", 450).attr("y", 150).attr("text-anchor", "middle").text("No entities in this selection");
    return;
  }
  const columns = new Map();
  const positions = new Map();
  for (const entry of entries) {
    let depth = depths?.get(entry.key) || 0, current = depths ? null : entry.parent;
    const visited = new Set([entry.key]);
    while (current && !visited.has(current.key)) {
      visited.add(current.key);
      depth++;
      current = current.parent;
    }
    if (!columns.has(depth)) columns.set(depth, []);
    columns.get(depth).push(entry);
  }
  const width = canvas.node().getBoundingClientRect().width || 900;
  const height = canvas.node().getBoundingClientRect().height || 320;
  const vertical = width < 600;
  const minimumDepth = Math.min(...columns.keys());
  for (const [depth, items] of columns)
    items.forEach((entry, index) => {
      if (!layout.positions.has(entry.key)) layout.positions.set(entry.key, vertical
        ? { x: width / 2 + index * 230, y: 45 + (depth - minimumDepth) * 110 }
        : { x: 115 + (depth - minimumDepth) * 260, y: 45 + index * 110 });
      positions.set(entry.key, layout.positions.get(entry.key));
    });
  canvas.attr("viewBox", `0 0 ${width} ${height}`);
  canvas.append("defs").append("marker").attr("id", "entity-arrow")
    .attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("refY", 0)
    .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto")
    .append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", "context-stroke");
  const group = canvas.append("g");
  entityZoom = d3.zoom().scaleExtent([0.1, 5]).on("zoom", (event) => {
    group.attr("transform", event.transform);
    layout.transform = event.transform;
  });
  canvas.call(entityZoom).on("dblclick.zoom", null);
  if (layout.transform) canvas.call(entityZoom.transform, layout.transform);
  else canvas.call(entityZoom.transform, d3.zoomIdentity);
  const drawnLinks = [];
  for (const link of links) {
    const typeLink = ["Type", "Component type", "Product reference", "Inherits"].includes(link.kind);
    const color = typeLink ? colors.import : ["Supply", "Allocated from", "Sourced from", "Received from"].includes(link.kind) ? colors.contribution : colors.replica;
    const path = group.append("path").attr("fill", "none").attr("stroke", color)
      .attr("stroke-width", 1.5).attr("stroke-dasharray", typeLink ? "5 4" : null).attr("marker-end", "url(#entity-arrow)");
    const plainLabel = { Type: "Defines asset", "Component type": "Component of", "Sourced from": "Supplies",
      "Received from": "Delivered as", "Allocated from": "Installed as",
      "Product reference": "Defines product", Inherits: "Contributes to", Event: "Records work on" };
    const label = group.append("text").attr("text-anchor", "middle").attr("class", "entity-link-label")
      .text(plainLabel[link.kind] || link.label);
    drawnLinks.push({ link: entityFlowLink(link), path, label });
  }
  function updateLinks() {
    for (const { link, path, label } of drawnLinks) {
      const from = positions.get(link.source), to = positions.get(link.target);
      const angle = Math.atan2(to.y - from.y, to.x - from.x);
      const x1 = from.x + Math.cos(angle) * 24, y1 = from.y + Math.sin(angle) * 24;
      const x2 = to.x - Math.cos(angle) * 28, y2 = to.y - Math.sin(angle) * 28;
      path.attr("d", `M${x1},${y1}L${x2},${y2}`);
      label.attr("x", (x1 + x2) / 2).attr("y", (y1 + y2) / 2 - 10);
    }
  }
  for (const entry of entries) {
    const position = positions.get(entry.key);
    const button = group.append("g").attr("transform", `translate(${position.x},${position.y})`)
      .datum(entry)
      .attr("tabindex", 0).attr("role", "button").attr("aria-label", entry.label + " / " + entityOwnerLabel(entry))
      .attr("class", "entity-node" + (state.asset === entry.key ? " selected" : ""));
    const select = () => {
      state.asset = entry.key;
      renderAssets();
      renderEntityNetwork();
      d3.select("#entity-network").selectAll(".entity-node").filter((item) => item.key === entry.key).node()?.focus();
      $("asset-inspector").scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    button.on("click", (event) => { if (!event.defaultPrevented) select(); })
      .on("contextmenu", (event) => { event.preventDefault(); event.stopPropagation(); showLineage(entry.key); })
      .on("keydown", (event) => {
      if (event.key === "ContextMenu" || event.key === "F10" && event.shiftKey || event.key.toLowerCase() === "l") {
        event.preventDefault(); showLineage(entry.key);
      } else if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); }
      else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const current = positions.get(entry.key), step = event.shiftKey ? 40 : 10;
        moveEntity(entry.key, current.x + (event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0),
          current.y + (event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0));
        positions.set(entry.key, layout.positions.get(entry.key));
        button.attr("transform", `translate(${positions.get(entry.key).x},${positions.get(entry.key).y})`);
        updateLinks();
      }
    });
    button.call(d3.drag().container(() => group.node()).subject(() => positions.get(entry.key)).clickDistance(4)
      .on("start", (event) => { event.sourceEvent.stopPropagation(); button.classed("dragging", true); })
      .on("drag", (event) => {
        moveEntity(entry.key, event.x, event.y);
        positions.set(entry.key, layout.positions.get(entry.key));
        button.attr("transform", `translate(${event.x},${event.y})`);
        updateLinks();
      }).on("end", () => button.classed("dragging", false)));
    button.append("circle").attr("r", 22);
    button.append("text").attr("text-anchor", "middle").attr("y", 5).text(entry.kind === "type" ? "T" : entry.kind === "facility" ? "F" : entry.kind === "asset" ? "A" : "E");
    button.append("text").attr("text-anchor", "middle").attr("y", 38).attr("class", "entity-node-label")
      .text(!state.lineage && entry.label.length > 23 ? entry.label.slice(0, 21) + "..." : entry.label);
    const product = entry.graph.entities[entry.path].components?.["urn:clip:construction:product-identity:v1"];
    button.append("text").attr("text-anchor", "middle").attr("y", 54).attr("class", "entity-node-kind")
      .text(entry.snapshot ? "Issued snapshot" : readable(entry.kind));
    button.append("text").attr("text-anchor", "middle").attr("y", 69).attr("class", "entity-node-owner")
      .text(entityOwnerLabel(entry));
    button.append("title").text(entry.label + "\n" + entry.path +
      "\nData owner: " + entityAuthority(entry) +
      (product ? "\nManufacturer: " + product.authorityDid + "\nProduct: " + product.recordId : "") +
      "\nDrag to move. Right-click or press L to show lineage.");
  }
  updateLinks();
  if (!hadTransform) fitEntityNetwork();
}

function canAcceptJoin(request, now = Date.now()) {
  return request.status === "pending" && request.inviteStatus === "pending" && Date.parse(request.expiresAt) > now;
}

async function loadProjectInvites() {
  const response = await api("/clip/v1/projects/" + encodeURIComponent(state.inviteProject.projectId) + "/invites");
  const list = $("issued-invites");
  list.replaceChildren();
  if (!response.items.length) empty(list, "No issued invites.", "ticket");
  for (const invite of response.items) {
    const row = node("div", undefined, "invite-row");
    const expired = Date.parse(invite.expiresAt) <= Date.now();
    const status = expired && ["active", "pending"].includes(invite.status) ? "expired" : invite.status;
    const body = node("div", undefined, "project-row-body");
    body.append(node("strong", readable(invite.role) + " / " + readable(status)), node("small", new Date(invite.expiresAt).toLocaleString()));
    row.append(body);
    if (["active", "pending"].includes(invite.status)) {
      const revoke = node("button", undefined, "secondary");
      revoke.type = "button";
      revoke.append(icon("ban"), node("span", "Revoke"));
      revoke.onclick = async () => {
        revoke.disabled = true;
        try {
          await api("/clip/v1/projects/invites/" + encodeURIComponent(invite.inviteId) + "/revoke", "POST");
          if (state.latestInviteId === invite.inviteId) { $("invite-result").hidden = true; $("invite-code").value = ""; }
          await loadProjectInvites();
        } catch (error) { $("invite-error").textContent = error.message; revoke.disabled = false; }
      };
      row.append(revoke);
    }
    list.append(row);
  }
  icons();
}

async function openProjectInvites(project) {
  state.inviteProject = project;
  state.latestInviteId = null;
  $("invite-form").reset();
  $("invite-title").textContent = project.name + " / Invite";
  $("invite-result").hidden = true;
  $("invite-error").textContent = "";
  $("invite-dialog").showModal();
  try { await loadProjectInvites(); }
  catch (error) { $("invite-error").textContent = error.message; }
}

async function openJoinRequests(project) {
  state.reviewProject = project;
  $("join-review-title").textContent = project.name + " / Join requests";
  $("join-review-error").textContent = "";
  if (!$("join-review-dialog").open) $("join-review-dialog").showModal();
  try {
    const response = await api("/clip/v1/projects/" + encodeURIComponent(project.projectId) + "/join-requests");
    const list = $("join-request-list");
    list.replaceChildren();
    if (!response.items.length) empty(list, "No join requests.", "users");
    for (const request of response.items) {
      const row = node("article", undefined, "join-request-row");
      row.append(node("h3", request.actorDid), node("span", readable(request.role) + " / " + readable(request.status), "badge"));
      if (request.status === "pending" && !canAcceptJoin(request)) row.append(node("p", "Invite " + (Date.parse(request.expiresAt) <= Date.now() ? "expired" : readable(request.inviteStatus)), "muted"));
      if (request.status === "pending") {
        const actions = node("div", undefined, "graph-tools");
        for (const decision of ["accept", "reject"]) {
          const button = node("button", undefined, decision === "reject" ? "secondary" : "");
          button.append(icon(decision === "accept" ? "user-check" : "user-x"), node("span", readable(decision)));
          button.disabled = decision === "accept" && !canAcceptJoin(request);
          button.onclick = async () => {
            for (const action of actions.querySelectorAll("button")) action.disabled = true;
            try {
              await api("/clip/v1/projects/join-requests/" + encodeURIComponent(request.joinRequestId) + "/decision", "POST", { decision, expectedRevision: response.revision });
              await refresh();
              await openJoinRequests(project);
            } catch (error) {
              $("join-review-error").textContent = error.message;
              for (const action of actions.querySelectorAll("button")) action.disabled = false;
              actions.querySelector("button").disabled = !canAcceptJoin(request);
            }
          };
          actions.append(button);
        }
        row.append(actions);
      }
      row.append(detail("Signed join request", request.request));
      if (request.decision) row.append(detail("Owner decision", request.decision));
      list.append(row);
    }
    icons();
  } catch (error) { $("join-review-error").textContent = error.message; }
}

function renderProjects() {
  $("new-project").disabled = false;
  $("new-project-menu").disabled = false;
  $("new-entity").disabled = !state.authorized || !state.projects.length;
  $("new-entity-menu").disabled = $("new-entity").disabled;
  const parent = $("project-list");
  parent.replaceChildren();
  if (!state.authorized) { empty(parent, "Local authorization required.", "lock-keyhole"); return; }
  if (!state.projects.length) { empty(parent, "No projects or product libraries.", "folder"); return; }
  for (const project of state.projects) {
    const row = node("article", undefined, "project-row");
    const text = node("div", undefined, "project-row-body");
    text.append(node("h2", project.name), node("small", project.projectId));
    const badges = node("div", undefined, "project-badges");
    badges.append(node("span", readable(project.visibility), "badge"), node("span", Object.keys(project.members).length + " participants", "muted"));
    text.append(badges);
    const actions = node("div", undefined, "graph-tools");
    const open = node("button", undefined, "secondary");
    open.append(icon("folder-open"), node("span", "Open"));
    open.onclick = () => { state.scope = project.projectId; $("scope").value = state.scope; state.facility = null; state.asset = null; state.lineage = null; render(); activateView("assets"); };
    const members = node("button", undefined, "secondary");
    members.append(icon("users"), node("span", "Participants"));
    members.onclick = () => openMembers(project);
    const invite = node("button", undefined, "secondary");
    invite.append(icon("ticket-plus"), node("span", "Invite"));
    invite.onclick = () => openProjectInvites(project);
    const requests = node("button", undefined, "secondary");
    requests.append(icon("user-check"), node("span", "Join requests" + (project.pendingJoinRequests ? " (" + project.pendingJoinRequests + ")" : "")));
    requests.onclick = () => openJoinRequests(project);
    actions.append(open, members, invite, requests);
    if (project.visibility === "private") {
      const installations = node("button", "Installations", "secondary");
      installations.onclick = () => {
        if (window.clipSupplyChain) window.clipSupplyChain.selectProject(project.projectId);
        activateView("installations");
      };
      const submissions = node("button", "Incoming / outgoing", "secondary");
      submissions.onclick = () => {
        if (window.clipSupplyChain) window.clipSupplyChain.selectProject(project.projectId);
        activateView("submissions");
      };
      actions.append(installations, submissions);
      const senders = node("button", "Submission senders", "secondary");
      senders.onclick = () => {
        if (window.clipSupplyChain) window.clipSupplyChain.openSenders(project).catch((error) => notice(error.message, true));
      };
      actions.append(senders);
    }
    row.append(icon(project.visibility === "private" ? "folder-lock" : "library"), text, actions);
    parent.append(row);
  }
}

function renderMembers() {
  $("member-list").replaceChildren();
  for (const [did, role] of Object.entries(state.editingMembers)) {
    const row = node("div", undefined, "member-row");
    const select = node("select");
    select.setAttribute("aria-label", "Role for " + did);
    for (const value of ["viewer", "contributor"]) { const option = node("option", readable(value)); option.value = value; select.append(option); }
    select.value = role;
    select.onchange = () => { state.editingMembers[did] = select.value; };
    const remove = node("button", undefined, "icon-button");
    remove.type = "button"; remove.title = "Remove " + did; remove.setAttribute("aria-label", remove.title);
    remove.append(icon("user-minus"));
    remove.onclick = () => { delete state.editingMembers[did]; renderMembers(); };
    row.append(node("span", did), select, remove);
    $("member-list").append(row);
  }
  icons();
}

function openMembers(project) {
  state.editingProject = project;
  state.editingMembers = { ...project.members };
  $("members-title").textContent = project.name;
  $("members-public").checked = project.visibility === "public";
  $("members-error").textContent = "";
  $("member-did").value = "";
  renderMembers();
  $("members-dialog").showModal();
}

function updateEntityChoices() {
  const project = state.projects.find((item) => item.projectId === $("entity-project").value);
  const graph = state.graphs.find((item) => item.datasetId === project?.projectId);
  const library = Boolean(graph?.effectiveComponents.library?.["urn:clip:construction:product-library:v1"]);
  const templates = state.templates?.items.filter((item) => !library || item.ifcClass.endsWith("Type")) || [];
  options("entity-template", templates, "id", (item) => item.name);
  updateEntityRelationships();
}

function updateEntityRelationships() {
  const project = state.projects.find((item) => item.projectId === $("entity-project").value);
  const graph = state.graphs.find((item) => item.datasetId === project?.projectId);
  const template = state.templates?.items.find((item) => item.id === $("entity-template").value);
  const className = template?.ifcClass;
  const paths = Object.keys(graph?.entities || {});
  const classOf = (path) => graph.effectiveComponents[path]?.["urn:clip:construction:source:v1"]?.class;
  options("entity-parent", [{ path: "", label: "Unassigned" }, ...paths.filter((path) => state.templates.parents[classOf(path)]?.includes(className))
    .map((path) => ({ path, label: entityLabel(graph, path) }))], "path", (item) => item.label);
  if (!$("entity-parent").value && [...$("entity-parent").options].some((option) => option.value === "project")) $("entity-parent").value = "project";
  const occurrence = ["IfcDoor", "IfcPump"].includes(className);
  options("entity-type", [{ path: "", label: "No product type" }, ...paths.filter((path) => occurrence && classOf(path) === className + "Type")
    .map((path) => ({ path, label: entityLabel(graph, path) }))], "path", (item) => item.label);
  $("entity-type").disabled = !occurrence;
  $("entity-access").textContent = project?.visibility === "private" ? "Private / inherited from project" : "Public / inherited from workspace";
  $("entity-class").textContent = className ? className + " / IFC4X3_ADD2" : "";
}

function renderInspector(entry) {
  const panel = $("asset-inspector");
  panel.replaceChildren();
  if (!entry) {
    const emptyBlock = node("div", undefined, "empty-inspector");
    emptyBlock.append(icon("mouse-pointer-2"), node("h2", "No asset selected"));
    panel.append(emptyBlock);
    return;
  }
  const heading = node("div", undefined, "inspector-heading"),
    title = node("div");
  title.append(node("h2", entry.label), node("small", entry.path));
  heading.append(icon(symbolFor(entry)), title);
  panel.append(heading);
  const components = entry.graph.effectiveComponents[entry.path] || {};
  function section(title) {
    const block = node("section", undefined, "inspector-section");
    block.append(node("h3", title));
    panel.append(block);
    return block;
  }
  function property(parent, label, value) {
    const list = node("dl", undefined, "property");
    list.append(node("dt", label), node("dd", value));
    parent.append(list);
  }
  const identity = section("Asset identity");
  property(identity, "Dataset", entry.graph.datasetId);
  const project = state.catalog.find((item) => item.datasetId === entry.graph.datasetId);
  if (project?.projectName) {
    property(identity, "Project / library", project.projectName);
    property(identity, "Access", readable(project.visibility) + " / inherited");
  }
  property(identity, "Authority", didLabel(entityAuthority(entry)));
  if (entry.contributions?.length > 1)
    for (const contribution of entry.contributions)
      property(identity, "Contributing record", contribution.graph.datasetId + " / " + contribution.path);
  if (entry.snapshot) property(identity, "Source record", "Read-only issued snapshot / not a local editable asset");
  property(identity, "Container", entry.parent?.label || "Unassigned");
  if (components["ifc::serial"])
    property(identity, "Serial", components["ifc::serial"]);
  const workflow = components["urn:clip:construction:source:v1"]?.properties?.supplyChain;
  if (workflow?.kind === "installation") {
    const serials = (workflow.sources || []).flatMap((source) => source.serials || []);
    if (serials.length) property(identity, "Allocated serials", serials.join(", "));
    if (workflow.data?.location) property(identity, "Installation location", workflow.data.location);
  }
  if (components["ifc::manufacturer"])
    property(identity, "Manufacturer", components["ifc::manufacturer"]);
  const manufacturerType = components["urn:clip:construction:product-identity:v1"];
  if (manufacturerType) {
    property(identity, "Manufacturer product", manufacturerType.authorityDid + " / " + manufacturerType.recordId);
    property(identity, "Resolved product revision", String(manufacturerType.revision));
    property(identity, "Pinned issue revisions", manufacturerType.pinnedRevisions.join(", "));
    property(identity, "Product view", entry.graph.productResolution?.mode || "imported revision");
  }
  const status = assetStatus(entry);
  if (status) property(identity, "Status", status);
  const sources = entitySources(entry),
    sourceBlock = section("Data origins");
  sourceBlock.append(
    node(
      "span",
      didLabel(entry.graph.authorityDid) + " / local dataset",
      "badge",
    ),
  );
  for (const source of sources) {
    const button = node("button", undefined, "reference-button");
    button.append(
      icon(source.kind === "import" ? "factory" : "hard-hat"),
      node("span", didLabel(source.did) + " / " + source.label),
    );
    button.onclick = () => {
      activateView("overview");
      selectOrigin(source.did);
      $("node-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    sourceBlock.append(button);
  }
  const relations = section("Relationships");
  const entity = entry.graph.entities[entry.path];
  const network = entityNetworkData();
  const related = new Map(network.entries.map((item) => [item.key, item]));
  for (const link of network.links.filter((item) => item.source === entry.key))
    property(relations, link.label, related.get(link.target).label + " / " + didLabel(entityAuthority(related.get(link.target))));
  if (entry.kind === "type")
    for (const link of network.links.filter((item) => item.target === entry.key && item.kind === "Type"))
      property(relations, "Type of", related.get(link.source).label);
  for (const [key, value] of Object.entries(components))
    if (
      typeof value === "string" &&
      /reference/.test(key) &&
      entry.graph.entities[value]
    ) {
      const target = {
        graph: entry.graph,
        path: value,
        kind: entityKind(entry.graph, value),
      };
      const button = node("button", undefined, "reference-button");
      button.append(
        icon(symbolFor(target)),
        node("span", entityLabel(entry.graph, value)),
      );
      button.onclick = () => {
        state.asset = entityKey(entry.graph, resolvePath(entry.graph, value));
        renderAssets();
        renderEntityNetwork();
      };
      relations.append(button);
    }
  if (
    !Object.keys(entity.inherits || {}).length &&
    relations.childElementCount === 1
  )
    relations.append(node("p", "No recorded relationships", "muted"));
  const history = state.history.filter((item) =>
    (entry.contributions || [{ graph: entry.graph, path: entry.path }]).some(({ graph, path }) =>
      item.datasetId === graph.datasetId && proposalTouchesPaths(item.transaction.proposal, inheritedPaths({ graph, path }))));
  if (history.length) {
    const historyBlock = section("Accepted history");
    for (const item of history) historyBlock.append(activityRow(item, true));
  }
  const componentBlock = section("Components");
  for (const [key, value] of Object.entries(components)) {
    if (typeof value === "object") componentBlock.append(detail(key, value));
    else property(componentBlock, readable(key), String(value));
  }
  panel.append(
    detail("Raw IFCX entity", { ...entity, effectiveComponents: components }),
  );
}
function renderReplicas() {
  const parent = $("replica-list");
  parent.replaceChildren();
  $("replicate").disabled =
    !state.authorized || !state.catalog.length || !state.peers.length;
  if (!state.authorized) {
    empty(parent, "Local authorization required.", "lock-keyhole");
    return;
  }
  const replicas = state.replicas.filter(
      (item) => !state.scope || item.datasetId === state.scope,
    ),
    acks = state.acknowledgements.filter(
      (item) => !state.scope || item.payload?.datasetId === state.scope,
    );
  if (!replicas.length && !acks.length)
    empty(parent, "No replica placements recorded.", "database");
  for (const item of replicas) {
    const row = node("article", undefined, "activity-row");
    row.append(icon("database"));
    const body = node("div", undefined, "activity-body");
    body.append(
      node("strong", readable(item.datasetId)),
      node("p", "Received from " + didLabel(item.authorityDid), "muted"),
      detail("Replica metadata", item),
    );
    row.append(body);
    parent.append(row);
  }
  for (const ack of acks) {
    const row = node("article", undefined, "activity-row");
    row.append(icon("copy-check"));
    const body = node("div", undefined, "activity-body");
    body.append(
      node("strong", "Acknowledged by " + didLabel(ack.actorDid)),
      node("p", ack.payload?.datasetId || "", "muted"),
      detail("Signed acknowledgement", ack),
    );
    row.append(body);
    parent.append(row);
  }
  icons();
}
function render() {
  renderNetwork();
  renderPortfolio();
  renderAssets();
  renderActivity();
  renderReplicas();
  renderProjects();
  renderEntityNetwork();
  icons();
}
function options(id, items, valueKey, labelFn) {
  const previous = $(id).value;
  $(id).replaceChildren();
  for (const item of items) {
    const option = node("option", labelFn(item));
    option.value = item[valueKey];
    $(id).append(option);
  }
  if (items.some((item) => item[valueKey] === previous)) $(id).value = previous;
}
async function refresh() {
  const request = ++state.request;
  $("refresh").disabled = true;
  try {
    const [info, catalog] = await Promise.all([
      api("/clip/v1/node/info"),
      api("/ifc/v1/datasets"),
    ]);
    const resolved = await Promise.allSettled(
      catalog.items.map(async (item) => {
        const base = "/ifc/v1/datasets/" + encodeURIComponent(item.datasetId);
        const [graph, history] = await Promise.all([
          api(base + "/graph" + (info.demoOpenAccess || $("key").value ? "?refresh_products=true" : "")),
          api(base + "/history"),
        ]);
        return {
          graph,
          history: history.items.map((record) => ({
            ...record,
            datasetId: item.datasetId,
          })),
        };
      }),
    );
    if (request !== state.request) return;
    state.info = info;
    if (info.demoOpenAccess) $("key").value = "";
    $("key-control").hidden = Boolean(info.demoOpenAccess);
    $("access-title").textContent = info.demoOpenAccess ? "Demo settings" : "Local authorization";
    $("access-submit").lastChild.textContent = info.demoOpenAccess ? "Save settings" : "Apply";
    $("access").title = info.demoOpenAccess ? "Demo storage settings" : "Local authorization and storage settings";
    $("access").setAttribute("aria-label", $("access").title);
    $("access").replaceChildren(icon(info.demoOpenAccess ? "hard-drive" : "key-round"));
    icons();
    state.catalog = catalog.items;
    state.graphs = [];
    state.history = [];
    const failures = [];
    resolved.forEach((result, index) => {
      if (result.status === "fulfilled") {
        state.graphs.push(result.value.graph);
        state.history.push(...result.value.history);
      } else
        failures.push(
          catalog.items[index].datasetId + ": " + result.reason.message,
        );
    });
    state.storage = info.encryptedStorageOptIn;
    $("storage").checked = state.storage;
    state.authorized = Boolean(info.demoOpenAccess);
    state.peers = [];
    state.replicas = [];
    state.acknowledgements = [];
    state.projects = [];
    try {
      const [peers, replication, projects] = await Promise.all([
        api("/clip/v1/network/gossip/peers"),
        api("/clip/v1/replication/status"),
        api("/clip/v1/projects"),
      ]);
      if (request !== state.request) return;
      state.peers = peers;
      state.replicas = replication.replicas;
      state.acknowledgements = replication.acknowledgements;
      state.authorized = true;
      state.projects = projects.items;
    } catch (error) {
      if (error.status !== 401 || $("key").value) failures.push(error.message);
    }
    $("authority-label").textContent = didLabel(info.did);
    $("authority-label").title = info.did;
    $("role").textContent = info.role;
    $("access").classList.toggle("authorized", state.authorized);
    const oldScope = state.scope;
    $("scope").replaceChildren(node("option", "All datasets"));
    $("scope").firstChild.value = "";
    for (const item of state.catalog) {
      const option = node("option", item.projectName || readable(item.datasetId));
      option.value = item.datasetId;
      $("scope").append(option);
    }
    state.scope = state.catalog.some((item) => item.datasetId === oldScope)
      ? oldScope
      : "";
    $("scope").value = state.scope;
    options("replication-dataset", state.catalog, "datasetId", (item) =>
      readable(item.datasetId),
    );
    options(
      "replication-peer",
      state.peers,
      "did",
      (item) => didLabel(item.did) + " / " + item.status,
    );
    $("updated").textContent =
      "Updated " +
      new Date().toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      });
    render();
    window.dispatchEvent(new CustomEvent("clip:dashboard-refresh"));
    if (request === 1 && ["manufacturer", "supplier", "contractor", "main_contractor", "owner", "client"].includes(info.role)) activateView("projects");
    notice(failures.length ? failures.join(" / ") : "", failures.length > 0);
    return !failures.length;
  } catch (error) {
    if (request === state.request) notice(error.message, true);
    return false;
  } finally {
    if (request === state.request) $("refresh").disabled = false;
  }
}
function activateView(id) {
  document.querySelectorAll("[data-view]").forEach((tab) => {
    const active = tab.dataset.view === id;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.hidden = view.id !== id;
    view.classList.toggle("active", !view.hidden);
  });
  if (id === "assets") renderEntityNetwork();
  window.dispatchEvent(new CustomEvent("clip:dashboard-view", { detail: { view: id } }));
}
window.addEventListener("resize", () => { if (!$("assets").hidden) renderEntityNetwork(); });
document.querySelectorAll("[data-view]").forEach((tab) => {
  tab.onclick = () => activateView(tab.dataset.view);
  tab.onkeydown = (event) => {
    const tabs = [...document.querySelectorAll("[data-view]")];
    let next;
    if (event.key === "ArrowRight")
      next = tabs[(tabs.indexOf(tab) + 1) % tabs.length];
    if (event.key === "ArrowLeft")
      next = tabs[(tabs.indexOf(tab) + tabs.length - 1) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs.at(-1);
    if (next) {
      event.preventDefault();
      activateView(next.dataset.view);
      next.focus();
    }
  };
});
$("scope").onchange = () => {
  state.scope = $("scope").value;
  state.facility = null;
  state.asset = null;
  state.networkDid = null;
  state.lineage = null;
  render();
};
$("asset-search").oninput = renderAssets;
$("refresh").onclick = refresh;
$("open-assets").onclick = () => activateView("assets");
$("open-activity").onclick = () => activateView("activity");
$("sources-mode").onclick = () => networkMode(false);
$("peers-mode").onclick = () => networkMode(true);
function networkMode(all) {
  state.allPeers = all;
  $("sources-mode").setAttribute("aria-pressed", String(!all));
  $("peers-mode").setAttribute("aria-pressed", String(all));
  renderNetwork();
}
$("fit-network").onclick = () =>
  d3.select("#network").call(networkZoom.transform, d3.zoomIdentity);
$("actions").onclick = () => {
  const open = $("action-menu").hidden;
  $("action-menu").hidden = !open;
  $("actions").setAttribute("aria-expanded", String(open));
};
document.addEventListener("click", (event) => {
  if (!event.target.closest(".header-actions")) {
    $("action-menu").hidden = true;
    $("actions").setAttribute("aria-expanded", "false");
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    $("action-menu").hidden = true;
    $("actions").setAttribute("aria-expanded", "false");
  }
});
$("access").onclick = () => {
  $("access-error").textContent = "";
  $("access-dialog").showModal();
};
$("access-dialog").addEventListener("close", () => { state.pendingProject = false; state.pendingInvite = false; });
document
  .querySelectorAll("[data-close]")
  .forEach(
    (button) => (button.onclick = () => $(button.dataset.close).close()),
  );
$("access-form").onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  try {
    if ($("storage").checked !== state.storage)
      await api("/clip/v1/node/storage/opt-in", "POST", {
        opt_in: $("storage").checked,
      });
    const success = await refresh();
    if (success && state.authorized) {
      const pendingProject = state.pendingProject;
      const pendingInvite = state.pendingInvite;
      state.pendingProject = false;
      state.pendingInvite = false;
      $("access-dialog").close();
      if (pendingProject) openProjectDialog();
      else if (pendingInvite) openJoinInvite();
    } else $("access-error").textContent = $("notice").textContent || "Enter a valid local API key.";
  } catch (error) {
    $("access-error").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
};
function openPayload(mode, title, value) {
  state.mode = mode;
  $("payload-title").textContent = title;
  $("payload").value = value;
  $("payload-error").textContent = "";
  $("action-menu").hidden = true;
  $("actions").setAttribute("aria-expanded", "false");
  $("payload-dialog").showModal();
}
$("register").onclick = () =>
  openPayload(
    "dataset",
    "Register IFCX dataset",
    JSON.stringify(
      {
        file: {
          header: {
            id: "urn:owner:dataset",
            ifcxVersion: "ifcx_alpha",
            dataVersion: "1.0.0",
            author: state.info?.did || "",
            timestamp: new Date().toISOString(),
          },
          imports: [],
          schemas: { "ifc::name": { value: { dataType: "String" } } },
          data: [
            {
              path: "asset",
              attributes: { "ifc::name": "Construction asset" },
            },
          ],
        },
        trustedProposers: [],
      },
      null,
      2,
    ),
  );
$("signed").onclick = () =>
  openPayload("transaction", "Submit signed transaction", "");

function openProjectDialog() {
  $("action-menu").hidden = true;
  $("actions").setAttribute("aria-expanded", "false");
  if (!state.authorized) {
    state.pendingInvite = false;
    state.pendingProject = true;
    $("access-error").textContent = "";
    $("access-dialog").showModal();
    return;
  }
  $("project-form").reset();
  $("project-kind").value = state.info?.role === "manufacturer" ? "product-library" : "project";
  $("project-public").checked = $("project-kind").value === "product-library";
  $("project-error").textContent = "";
  $("action-menu").hidden = true;
  $("actions").setAttribute("aria-expanded", "false");
  $("project-dialog").showModal();
}
$("new-project").onclick = openProjectDialog;
$("new-project-menu").onclick = openProjectDialog;
function openJoinInvite() {
  $("action-menu").hidden = true;
  $("actions").setAttribute("aria-expanded", "false");
  if (!state.authorized) {
    state.pendingProject = false;
    state.pendingInvite = true;
    $("access-error").textContent = "";
    $("access-dialog").showModal();
    return;
  }
  $("join-form").reset();
  $("join-result").hidden = true;
  $("join-error").textContent = "";
  $("join-dialog").showModal();
}
$("join-project").onclick = openJoinInvite;
$("join-project-menu").onclick = openJoinInvite;
$("refresh-joins").onclick = () => openJoinRequests(state.reviewProject);
$("invite-form").onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  $("invite-error").textContent = "";
  try {
    const invite = await api("/clip/v1/projects/" + encodeURIComponent(state.inviteProject.projectId) + "/invites", "POST", {
      role: $("invite-role").value, expiresHours: Number($("invite-hours").value),
    });
    state.latestInviteId = invite.inviteId;
    $("invite-code").value = invite.code;
    $("invite-expiry-note").textContent = "Single use / Expires " + new Date(invite.expiresAt).toLocaleString();
    $("invite-result").hidden = false;
    await loadProjectInvites();
  } catch (error) { $("invite-error").textContent = error.message; }
  finally { event.submitter.disabled = false; }
};
$("copy-invite").onclick = async () => {
  try { await navigator.clipboard.writeText($("invite-code").value); $("copy-invite").title = "Copied"; }
  catch { $("invite-code").focus(); $("invite-code").select(); }
};
$("join-form").onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  $("join-error").textContent = "";
  $("join-result").hidden = true;
  try {
    const response = await api("/clip/v1/projects/invites/redeem", "POST", { code: $("join-code").value.trim() });
    const result = response.payload;
    $("join-result").textContent = result.projectName + " / " + readable(result.role) + " / Awaiting owner approval";
    $("join-result").hidden = false;
    $("join-code").value = "";
    window.dispatchEvent(new CustomEvent("clip:dashboard-refresh"));
  } catch (error) { $("join-error").textContent = error.message; }
  finally { event.submitter.disabled = false; }
};
$("project-kind").onchange = () => { $("project-public").checked = $("project-kind").value === "product-library"; };
$("project-form").onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const project = await api("/clip/v1/projects", "POST", { name: $("project-name").value.trim(), kind: $("project-kind").value, visibility: $("project-public").checked ? "public" : "private" });
    state.scope = project.projectId;
    $("project-dialog").close();
    await refresh();
    activateView("projects");
  } catch (error) { $("project-error").textContent = error.message; }
  finally { event.submitter.disabled = false; }
};
$("add-member").onclick = () => {
  const did = $("member-did").value.trim();
  if (!did.startsWith("did:web:") || did === state.info?.did) { $("members-error").textContent = "Enter a different organisation's did:web identity."; return; }
  state.editingMembers[did] = $("member-role").value;
  $("member-did").value = "";
  $("members-error").textContent = "";
  renderMembers();
};
$("members-form").onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    if ($("member-did").value.trim()) throw new Error("Add the entered participant before saving.");
    await api("/clip/v1/projects/" + encodeURIComponent(state.editingProject.projectId) + "/permissions", "PUT", {
      visibility: $("members-public").checked ? "public" : "private", members: state.editingMembers, expectedRevision: state.editingProject.revision,
    });
    $("members-dialog").close();
    await refresh();
  } catch (error) { $("members-error").textContent = error.message; }
  finally { event.submitter.disabled = false; }
};
async function openEntityDialog() {
  $("action-menu").hidden = true;
  $("actions").setAttribute("aria-expanded", "false");
  try {
    state.templates = await api("/ifc/v1/projects/templates");
    $("entity-form").reset();
    options("entity-project", state.projects, "projectId", (item) => item.name);
    if (state.projects.some((item) => item.projectId === state.scope)) $("entity-project").value = state.scope;
    updateEntityChoices();
    $("entity-error").textContent = "";
    $("entity-dialog").showModal();
  } catch (error) { notice(error.message, true); }
}
$("new-entity").onclick = openEntityDialog;
$("new-entity-menu").onclick = openEntityDialog;
$("entity-project").onchange = updateEntityChoices;
$("entity-template").onchange = updateEntityRelationships;
$("entity-form").onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const projectId = $("entity-project").value;
    const result = await api("/ifc/v1/projects/" + encodeURIComponent(projectId) + "/entities", "POST", {
      template: $("entity-template").value, name: $("entity-name").value.trim(),
      parentPath: $("entity-parent").value || null, typePath: $("entity-type").value || null,
    });
    state.scope = projectId;
    state.asset = entityKey({ authorityDid: state.info.did, datasetId: projectId }, result.entityPath);
    $("entity-dialog").close();
    await refresh();
    activateView("assets");
  } catch (error) { $("entity-error").textContent = error.message; }
  finally { event.submitter.disabled = false; }
};
$("fit-entities").onclick = fitEntityNetwork;
$("show-lineage").onclick = () => { if (state.asset) showLineage(state.asset); };
$("show-all-entities").onclick = () => { state.lineage = null; renderEntityNetwork(); };
$("reset-entity-layout").onclick = () => {
  state.entityLayouts.delete(entityLayoutKey());
  renderEntityNetwork();
  fitEntityNetwork();
};
$("entity-network").onkeydown = (event) => {
  if (event.key === "Escape" && state.lineage) {
    event.preventDefault();
    state.lineage = null;
    renderEntityNetwork();
    $("show-lineage").focus();
  }
};
$("payload-form").onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const body = JSON.parse($("payload").value),
      path =
        state.mode === "dataset"
          ? "/datasets"
          : body.decision
            ? "/decisions"
            : "/proposals";
    await api("/ifc/v1" + path, "POST", body);
    $("payload-dialog").close();
    await refresh();
  } catch (error) {
    $("payload-error").textContent = error.message;
  } finally {
    event.submitter.disabled = false;
  }
};
$("replicate").onclick = async () => {
  const button = $("replicate");
  button.disabled = true;
  try {
    await api("/clip/v1/replication/push", "POST", {
      datasetId: $("replication-dataset").value,
      peerDid: $("replication-peer").value,
    });
    await refresh();
    notice("Replica acknowledged.");
  } catch (error) {
    notice(error.message, true);
  } finally {
    renderReplicas();
  }
};
matchMedia("(max-width:760px)").addEventListener("change", renderNetwork);
icons();
refresh();

// The operator key is used only with the local broker; organisation signing stays server-side.
(() => {
  const base = "/clip/v1/supply-chain";
  const views = new Set(["products", "supplied", "discover", "documents", "installations", "submissions"]);
  const kinds = { product: "Product definition", offering: "Supplier offering", supply: "Project delivery", installation: "Installation", asset: "Accepted asset" };
  const fields = {
    product: [["model", "Model"], ["sku", "Manufacturer SKU"], ["description", "Description", "textarea"], ["manufacturer", "Manufacturer name"]],
    offering: [["sku", "Supplier SKU"], ["description", "Supplier description", "textarea"], ["warranty", "Warranty"], ["service", "Service information"]],
    supply: [["quantity", "Delivered quantity", "number", true], ["unit", "Unit", "text", true], ["batch", "Batch"], ["serials", "Serial numbers (one per line)", "serials"], ["deliveryDate", "Delivery date", "date"], ["location", "Delivery location"], ["status", "Delivery status", ["planned", "dispatched", "delivered"]]],
    installation: [["quantity", "Installed / allocated quantity", "number", true], ["unit", "Unit", "text", true], ["serials", "Allocated serials (one per line)", "serials"], ["location", "Installation location", "text", true], ["installationDate", "Installation date", "date"], ["installer", "Installer"], ["status", "Technical status", ["planned", "in-progress", "complete", "failed"]], ["commissioningDate", "Commissioning date", "date"], ["commissioningNotes", "Commissioning notes", "textarea"]],
    asset: [["location", "Location"], ["status", "Technical status", ["planned", "in-progress", "complete", "failed"]], ["description", "Description", "textarea"]],
  };
  const sc = { records: [], catalogue: [], projects: [], submissions: [], revisions: [], schema: null, loaded: false, stale: true, request: 0, loading: null, dialog: null, discovered: new Map(), keys: new Map(), deliveryRequests: new Map() };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const pinKey = (value) => JSON.stringify([value.authorityDid, value.recordId || value.id, value.revision]);
  const projectKey = (value) => JSON.stringify([value.authorityDid, value.projectId]);
  const label = (value) => kinds[value] || readable(value);
  const sourcePin = (record) => ({ authorityDid: record.authorityDid, recordId: record.id, revision: record.revision });
  const serials = (value) => String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  function isOwned(record, did = state.info?.did) { return Boolean(did && record.authorityDid === did); }
  function technicalStatus(record) {
    if (record.kind === "supply") return "Delivery: " + (record.data.status || "Not recorded") + " / not an installation";
    if (record.kind === "installation" || record.kind === "asset")
      return "Technical status: " + (record.data.status || "Not recorded") + " (independent of acceptance)";
    return "Working state: " + record.status;
  }
  function eligibleSources(kind, candidates, recordId, projectId, authorityDid = state.info?.did) {
    const allowed = { product: ["product", "offering"], offering: ["product", "offering"], supply: ["offering"], installation: ["supply", "installation", "asset"], asset: ["supply", "installation", "asset"] };
    return candidates.filter((item) => allowed[kind]?.includes(item.kind) && !(item.id === recordId && item.authorityDid === authorityDid) &&
      (["installation", "asset"].includes(kind) ? Boolean(item.acceptedFrom) && (!projectId || item.projectId === projectId) : item.status === "published"));
  }
  function decimalParts(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Allocation quantities must be finite numbers.");
    const [mantissa, exponent = "0"] = String(value).toLowerCase().split("e");
    const fraction = mantissa.split(".")[1]?.length || 0;
    return { coefficient: BigInt(mantissa.replace(".", "")), scale: fraction - Number(exponent) };
  }
  function decimalSumExceeds(values, limit) {
    const parts = [...values, limit].map(decimalParts);
    const scale = Math.max(...parts.map((item) => item.scale));
    const integers = parts.map((item) => item.coefficient * 10n ** BigInt(scale - item.scale));
    return integers.slice(0, -1).reduce((sum, value) => sum + value, 0n) > integers.at(-1);
  }
  function assertAllocation(data, sources, candidates, existing = [], recordId) {
    if (!(Number.isFinite(data.quantity) && data.quantity > 0) || !data.unit?.trim())
      throw new Error("Enter a positive quantity and unit.");
    if (new Set(data.serials || []).size !== (data.serials || []).length || (data.serials || []).length > data.quantity)
      throw new Error("Serials must be unique and must not exceed the quantity.");
    for (const source of sources) {
      const upstream = candidates.find((item) => pinKey(item) === pinKey(source));
      if (!upstream) throw new Error("The selected immutable source revision is unavailable. Refresh and select it explicitly.");
      if (upstream.kind !== "supply") continue;
      if (!upstream.acceptedFrom) throw new Error("Installation requires accepted supply, not just a delivered submission.");
      if (data.unit !== upstream.data.unit) throw new Error("Installation units must match the accepted supply.");
      const allocations = existing.filter((item) => item.kind === "installation" && item.id !== recordId).flatMap((item) => item.sources || [])
        .filter((item) => item.authorityDid === source.authorityDid && item.recordId === source.recordId);
      if (decimalSumExceeds([...allocations.map((item) => item.quantity ?? 0), data.quantity], upstream.data.quantity))
        throw new Error("Allocation exceeds accepted, unallocated supply quantity.");
      const used = new Set(allocations.flatMap((item) => item.serials || []));
      const available = new Set(upstream.data.serials || []);
      if ((data.serials || []).some((item) => used.has(item) || !available.has(item))) throw new Error("A serial is already allocated or is not part of this accepted supply.");
    }
  }
  function button(text, action, className = "secondary") {
    const result = node("button", text, className);
    result.type = "button";
    result.onclick = async () => {
      result.disabled = true;
      try { await action(); }
      catch (error) { showError(error); }
      finally { result.disabled = false; }
    };
    return result;
  }
  function showError(error) {
    const message = error.message + (error.status === 409 ? " Refresh and compare the current revision before retrying. Your unsaved input has not been applied." : error.status === 424 ? " Resolve the unavailable source or evidence grant before retrying." : "");
    if ($("supply-dialog").open) {
      $("supply-dialog-error").textContent = message;
      if (error.status === 409) {
        const current = sc.dialog;
        $("supply-dialog-error").append(button("Refresh current record (discard unsaved form)", async () => {
          await load(true);
          if (current?.adopting) await openAdopt();
          else if (current?.senders) await openSenders(current.senders);
          else if (current?.grant) await openDocumentGrants(current.grant.record, current.grant.document);
          else if (current?.record) {
            const fresh = sc.records.find((item) => item.id === current.record.id);
            if (!fresh) throw new Error("The owned record is no longer available.");
            if (current.mode === "publish") openPublish(fresh);
            else if (current.mode === "freeze") openFreeze(fresh);
            else if (current.mode === "document") openDocument(fresh);
            else if (current.mode === "retire") {
              const document = fresh.documents.find((item) => item.id === current.document.id);
              if (!document) throw new Error("Attachment is no longer current; refresh its version history.");
              openRetireDocument(fresh, document);
            }
            else await openRecord(current.kind || fresh.kind, fresh);
          }
          else if (current?.submission) await openSubmissionDetail(sc.submissions.find((item) => item.id === current.submission.id));
        }));
      }
    } else notice(message, true);
    if (error.status === 409) sc.stale = true;
  }
  function requireAccess() {
    if (state.authorized) return true;
    $("access-error").textContent = "Local authorization is required to use private supply-chain records.";
    $("access-dialog").showModal();
    return false;
  }
  function status(message, error = false) {
    for (const id of ["products", "supplied", "catalogue", "documents", "installations", "submissions"]) {
      $(id + "-status").textContent = message;
      $(id + "-status").classList.toggle("form-error", error);
    }
  }
  function items(value, context) {
    if (!value || !Array.isArray(value.items)) throw new Error(context + " returned an invalid items list.");
    return value.items;
  }
  function mergeCatalogue() {
    const unique = new Map(sc.catalogue.map((item) => [pinKey(item), item]));
    for (const list of sc.discovered.values()) for (const item of list) unique.set(pinKey(item), item);
    return [...unique.values()];
  }
  async function load(force = false) {
    if (sc.loading && !force) return sc.loading;
    const request = ++sc.request;
    status("Refreshing supply-chain records...");
    sc.loading = (async () => {
      try {
        const [catalogueResponse, schema] = await Promise.all([api(base + "/catalogue"), api(base + "/schema")]);
        const catalogue = items(catalogueResponse, "Catalogue");
        if (!Array.isArray(schema.kinds) || !Array.isArray(schema.typeClasses) || !Array.isArray(schema.occurrenceClasses))
          throw new Error("Supply-chain schema returned an invalid supported-class contract.");
        if (!state.authorized) {
          if (request !== sc.request) return;
          sc.catalogue = catalogue; sc.schema = schema;
          sc.records = []; sc.projects = []; sc.submissions = []; sc.revisions = [];
          sc.discovered.clear();
          sc.loaded = true; sc.stale = false;
          renderSupply();
          status("Public catalogue only. Local authorization is required to author records, discover partner catalogues or review submissions.");
          return;
        }
        const [records, projects, submissions] = await Promise.all([
          api(base + "/records"), api(base + "/projects"), api(base + "/submissions"),
        ]);
        const owned = items(records, "Records");
        const revisions = await Promise.all(owned.map(async (record) => items(await api(base + "/records/" + encodeURIComponent(record.id) + "/revisions"), "Record revisions")));
        if (request !== sc.request) return;
        sc.catalogue = catalogue; sc.schema = schema; sc.records = owned; sc.projects = items(projects, "Projects");
        sc.submissions = items(submissions, "Submissions"); sc.revisions = revisions.flat();
        sc.loaded = true; sc.stale = false;
        renderSupply();
        status("Updated " + new Date().toLocaleTimeString() + " / working drafts and selected immutable revisions are separate.");
      } catch (error) {
        if (request === sc.request) {
          sc.stale = true;
          status("Refresh failed: " + error.message + (sc.loaded ? " Displayed data may be stale; refresh before acting." : " No supply-chain data has been loaded."), true);
          renderSupply();
        }
        throw error;
      } finally { if (request === sc.request) sc.loading = null; }
    })();
    return sc.loading;
  }
  function emptyList(parent, text) { parent.append(node("p", text, "supply-empty")); }
  function candidates() {
    const map = new Map([...mergeCatalogue(), ...sc.revisions].map((item) => [pinKey(item), item]));
    return [...map.values()];
  }
  function issuePreview(record) {
    return sc.revisions.find((item) => pinKey(item) === pinKey(record)) || record;
  }
  function destinationEligible(project) {
    return Boolean(project.local || !project.status || project.status === "accepted");
  }
  function projectOptions(select, localOnly = false, includeAll = true, destinationsOnly = false) {
    const old = select.value;
    select.replaceChildren();
    if (includeAll) { const option = node("option", "All projects"); option.value = ""; select.append(option); }
    for (const project of sc.projects.filter((item) => (!localOnly || item.local && item.visibility === "private") && (!destinationsOnly || destinationEligible(item)))) {
      const option = node("option", (project.name || project.projectName || project.projectId) + " / " + (project.local ? "local" : project.authorityDid) + (project.status ? " / " + project.status : ""));
      option.value = localOnly ? project.projectId : projectKey(project);
      select.append(option);
    }
    if ([...select.options].some((item) => item.value === old)) select.value = old;
  }
  function recordCard(record, publicOnly = false) {
    const card = node("article", undefined, "supply-record");
    const heading = node("div", undefined, "supply-record-heading");
    heading.append(node("h2", record.name), node("span", label(record.kind), "supply-badge"));
    const meta = node("div", undefined, "supply-record-meta");
    meta.append(node("span", "Revision " + record.revision + (publicOnly ? " / immutable" : " / working record"), "supply-badge"),
      node("span", publicOnly ? "Public / read-only source" : "Private working record / local control", "supply-badge"));
    if (record.acceptedFrom) meta.append(node("span", "Recipient-owned / accepted " + label(record.originalKind), "supply-badge"));
    card.append(heading, meta, node("p", "Controlled by: " + record.authorityDid, "muted"), node("p", record.ifcClass + " / " + technicalStatus(record), "muted"));
    if (record.sources?.length) card.append(lineage(record));
    const actions = node("div", undefined, "supply-record-actions");
    actions.append(button("View " + (publicOnly ? "revision" : "record"), () => openRecordDetail(record, publicOnly)));
    if (!publicOnly && isOwned(record) && !sc.stale) {
      actions.append(button("Edit data / sources", () => openRecord(record.kind, record)));
      actions.append(button("Add document", () => openDocument(record)));
      actions.append(button("Freeze private revision", () => openFreeze(record)));
      if (["product", "offering"].includes(record.kind) && !record.projectId)
        actions.append(button("Publish public revision", () => openPublish(record)));
      actions.append(button("Submit onward", () => openSubmission(record)));
      if (record.kind === "supply" && record.acceptedFrom) actions.append(button("Record installation", () => openRecord("installation", null, record)));
    }
    if (publicOnly) {
      if (record.kind === "product" || record.kind === "offering") actions.append(button("Reuse as my offering", () => openRecord("offering", null, record)));
    }
    card.append(actions);
    return card;
  }
  function lineage(record) {
    const panel = node("div", undefined, "supply-lineage");
    const chain = [...(record.lineage || [])];
    panel.append(node("strong", "Pinned source chain"));
    for (const source of record.sources || []) {
      const snapshot = candidates().find((item) => pinKey(item) === pinKey(source)) ||
        record.dependencies?.find((item) => pinKey(item) === pinKey(source));
      panel.append(node("p", (snapshot?.name || source.recordId) + " / " + source.authorityDid + " / revision " + source.revision +
        (snapshot ? " / " + label(snapshot.kind) : " / restricted or unavailable detail") +
        (source.quantity ? " / " + source.quantity + " " + source.unit : "")));
      const newer = candidates().filter((item) => item.id === source.recordId && item.authorityDid === source.authorityDid && item.revision > source.revision);
      if (newer.length) panel.append(node("p", "Newer revision " + Math.max(...newer.map((item) => item.revision)) + " available. Saved revision " + source.revision + " remains selected until explicitly reviewed.", "supply-field-help"));
    }
    const manufacturers = [...new Set(chain.filter((item) => item.kind === "product").map((item) => item.authorityDid))];
    const suppliers = [...new Set(chain.filter((item) => item.kind === "offering").map((item) => item.authorityDid))];
    if (record.kind === "product" && !record.acceptedFrom) manufacturers.push(record.authorityDid);
    if (manufacturers.length) panel.append(node("p", "Originating manufacturer: " + [...new Set(manufacturers)].join(", ")));
    if (suppliers.length) panel.append(node("p", "Supply chain (upstream to immediate): " + suppliers.join(" -> ")));
    if (record.acceptedFrom) panel.append(node("p", "Accepted from " + record.acceptedFrom.snapshot.authorityDid + " / issue " + record.acceptedFrom.issue.submissionId + ". Acceptance is not technical certification or completion."));
    return panel;
  }
  function renderSupply() {
    for (const [kind, id] of [["product", "product-list"], ["offering", "offering-list"], ["supply", "supply-list"]]) {
      const parent = $(id); parent.replaceChildren();
      const selected = sc.records.filter((item) => item.kind === kind);
      for (const item of selected) parent.append(recordCard(item));
      if (!selected.length) emptyList(parent, $("key").value ? "No " + label(kind).toLowerCase() + " records yet." : "Local authorization required.");
    }
    projectOptions($("installation-project"), true);
    projectOptions($("submission-project"));
    renderInstallations(); renderCatalogue(); renderSubmissions(); renderDocuments();
    renderDestinations();
    for (const id of ["create-product", "create-offering", "create-supply", "create-installation", "create-submission"])
      $(id).disabled = sc.stale;
    icons();
  }
  function renderInstallations() {
    const parent = $("installation-list"); parent.replaceChildren();
    const projectId = $("installation-project").value;
    const received = sc.records.filter((item) => item.kind === "supply" && item.acceptedFrom && (!projectId || item.projectId === projectId));
    if (received.length) {
      const heading = node("h2", "Accepted products received / not installed", "supply-list-heading");
      parent.append(heading);
      for (const item of received) parent.append(recordCard(item));
      parent.append(node("h2", "Recorded installations and accepted assets", "supply-list-heading"));
    }
    const selected = sc.records.filter((item) => ["installation", "asset"].includes(item.kind) && (!projectId || item.projectId === projectId));
    for (const item of selected) parent.append(recordCard(item));
    if (!selected.length) emptyList(parent, "No installation or accepted asset records in this project. Accept supply first, then record the actual installation.");
  }
  function renderCatalogue() {
    const parent = $("catalogue-list"); parent.replaceChildren();
    const search = $("catalogue-search").value.toLowerCase().trim();
    const kind = $("catalogue-kind").value;
    const selected = mergeCatalogue().filter((item) => (!kind || item.kind === kind) && JSON.stringify([item.name, item.authorityDid, item.ifcClass, item.data?.model, item.data?.sku]).toLowerCase().includes(search));
    for (const item of selected) parent.append(recordCard(item, true));
    if (!selected.length) emptyList(parent, "No matching published revisions. Discover a manufacturer's or supplier's catalogue using its organisation DID.");
  }
  function renderSubmissions() {
    const parent = $("submission-list"); parent.replaceChildren();
    const direction = $("submission-direction").value;
    const project = $("submission-project").value;
    const stateFilter = $("submission-status-filter").value;
    const selected = sc.submissions.filter((item) => (!stateFilter || item.status === stateFilter) &&
      (direction === "all" || item.direction === direction || item.localRecipient) &&
      (!project || project === JSON.stringify([item.recipientDid, item.projectId])));
    for (const item of selected) {
      const card = node("article", undefined, "supply-record");
      card.append(node("h2", (item.localRecipient ? "Local sender / local recipient" : item.direction === "incoming" ? "Incoming" : "Outgoing") + " / " + readable(item.status)),
        node("p", "From " + item.senderDid + " -> " + item.recipientDid, "muted"),
        node("p", "Project " + item.projectId + " / issue " + item.id + " / revision " + item.revision, "muted"),
        node("p", "Delivery acknowledgement: " + (item.deliveryStatus || (item.direction === "incoming" ? "received" : "not issued")) + " / decision: " + (item.decision?.decision || "pending"), "muted"));
      if (item.supersedes) card.append(node("p", "Corrects issue " + item.supersedes, "muted"));
      if (item.decision?.reason) card.append(node("p", "Recipient reason: " + item.decision.reason));
      card.append(button("Review scope / decision", () => openSubmissionDetail(item)));
      parent.append(card);
    }
    if (!selected.length) emptyList(parent, "No matching submissions. Incoming reviews and outgoing issues are separate from project invitations.");
  }
  function renderDocuments() {
    const parent = $("document-list"); parent.replaceChildren();
    for (const record of sc.records) for (const document of record.documents || []) {
      const card = node("article", undefined, "supply-record");
      card.append(node("h2", document.name), node("p", "Record: " + record.name + " / record revision " + record.revision, "muted"),
        documentDescription(document), button("Read / download", () => downloadDocument(record, document)));
      if (document.authorityDid === state.info?.did) card.append(button("Recipients / expiry / revoke", () => openDocumentGrants(record, document)));
      if (canManageAttachment(record, document)) card.append(button("Retire attachment from next revision", () => openRetireDocument(record, document)));
      parent.append(card);
    }
    if (!parent.childElementCount) emptyList(parent, "No documents attached. Use Add document on a local record; deliberately public datasheets are separate from private project evidence.");
  }
  function renderDestinations() {
    const parent = $("supply-destinations");
    if (!parent) return;
    parent.replaceChildren();
    const remote = sc.projects.filter((item) => !item.local);
    if (!remote.length) { emptyList(parent, "No linked remote destinations. Accept a project invite, then link the recipient organisation DID and project ID."); return; }
    for (const project of remote) {
      const card = node("article", undefined, "supply-record");
      card.append(node("h2", project.name || project.projectName || project.projectId), node("p", project.authorityDid + " / " + project.projectId, "muted"),
        node("p", "Invitation / connection: " + (project.status || "accepted") + " / requested role: " + (project.role || "not specified") + ". Membership and scoped sender approval are separate from submission acceptance.", "supply-lineage"),
        button("Refresh signed invitation / access status", async () => {
          const result = await api(base + "/projects/refresh", "POST", { authorityDid: project.authorityDid, projectId: project.projectId });
          await load(true); notice("Recipient-side project status: " + (result.status || "accepted") + ". No product or installation acceptance is implied.");
        }),
        button("View directed submissions", () => { $("submission-project").value = projectKey(project); activateView("submissions"); renderSubmissions(); }));
      if (destinationEligible(project)) card.append(button("Prepare submission to this project", () => { openSubmission(); const select = $("supply-dialog-body").querySelector("select"); if (select) { select.value = projectKey(project); select.onchange(); } }));
      else card.append(node("p", "Not eligible as a submission destination while invitation status is " + project.status + ". Refresh after the project owner decides.", "muted"));
      if (project.joinRequestId) card.append(node("p", "Join request: " + project.joinRequestId + " / signed acknowledgement: " + (project.acknowledgement ? "recorded" : "not provided") + " / owner decision: " + (project.decision ? "recorded" : "pending"), "muted"));
      parent.append(card);
    }
  }
  function selectProject(projectId) {
    $("installation-project").value = projectId;
    const project = sc.projects.find((item) => item.local && item.projectId === projectId);
    if (project) $("submission-project").value = projectKey(project);
    renderInstallations(); renderSubmissions();
  }
  function documentDescription(document) {
    const panel = node("div", undefined, "supply-lineage");
    panel.append(node("p", "Issuer: " + document.authorityDid + " / " + document.visibility + " / " + document.mediaType),
      node("p", "Document version ID: " + document.id + " / " + document.size + " bytes"),
      node("p", document.supersedesDocumentId ? "Replaces document version: " + document.supersedesDocumentId : "No preceding document version specified"),
      node("p", "Integrity digest: " + document.digest),
      node("p", "Physical retention duration is not specified by this document service. Grant expiry limits future reads, not stored bytes or downloaded copies. Recipient access is checked on read; public record visibility alone does not grant private evidence."));
    return panel;
  }
  function dialog(title, step = "", submitText = "Save") {
    $("supply-form").reset();
    $("supply-dialog-title").textContent = title;
    $("supply-dialog-step").textContent = step;
    $("supply-dialog-body").replaceChildren();
    $("supply-dialog-error").replaceChildren();
    $("supply-dialog-back").hidden = true;
    $("supply-dialog-next").hidden = true;
    $("supply-dialog-submit").hidden = true;
    $("supply-dialog-submit").disabled = false;
    $("supply-dialog-submit").textContent = submitText;
    $("supply-form").onsubmit = null;
    sc.dialog = null;
    if (!$("supply-dialog").open) $("supply-dialog").showModal();
  }
  function field(parent, title, type = "text", value = "", required = false) {
    const wrapper = node("label", title);
    let control;
    if (Array.isArray(type)) {
      control = node("select");
      const values = value && !type.includes(value) ? [value, ...type] : type;
      for (const item of values) { const option = node("option", readable(item)); option.value = item; control.append(option); }
    } else if (["textarea", "serials"].includes(type)) control = node("textarea");
    else { control = node("input"); control.type = type; if (type === "number") { control.min = "0.000001"; control.step = "any"; } }
    control.value = type === "serials" ? (value || []).join("\n") : value ?? "";
    control.setAttribute("aria-label", title);
    control.required = required;
    wrapper.append(control); parent.append(wrapper);
    return control;
  }
  function checkbox(parent, title, checked = false) {
    const wrapper = node("label", undefined, "supply-check");
    const input = node("input"); input.type = "checkbox"; input.checked = checked;
    wrapper.append(input, node("span", title)); parent.append(wrapper); return input;
  }
  function showValues(parent, value, prefix = "") {
    for (const [key, item] of Object.entries(value || {})) {
      if (item && typeof item === "object" && !Array.isArray(item)) showValues(parent, item, prefix + readable(key) + " / ");
      else { const row = node("dl", undefined, "property"); row.append(node("dt", prefix + readable(key)), node("dd", Array.isArray(item) ? item.map((entry) => typeof entry === "object" ? Object.entries(entry).map(([k, v]) => readable(k) + ": " + String(v)).join(", ") : entry).join("; ") : String(item ?? "Not set"))); parent.append(row); }
    }
  }
  function recursivePropertyEditor(parent, initial) {
    const controls = [];
    function build(value, path = []) {
      for (const [key, item] of Object.entries(value)) {
        if (item && typeof item === "object" && !Array.isArray(item)) build(item, [...path, key]);
        else if (Array.isArray(item) && item.some((entry) => typeof entry !== "string")) {
          build(item, [...path, key]);
        } else {
          const type = typeof item === "boolean" ? "checkbox" : typeof item === "number" ? "number" : Array.isArray(item) ? "serials" : "text";
          const input = type === "checkbox" ? checkbox(parent, [...path, key].join(" / "), item) : field(parent, [...path, key].join(" / "), type, item);
          if (type === "number") input.removeAttribute("min");
          const entry = { path: [...path, key], type, input, original: item, removed: false };
          controls.push(entry);
          if (!Array.isArray(value)) parent.append(button("Remove property " + [...path, key].join(" / "), () => {
            entry.removed = true; input.disabled = true; input.parentElement.hidden = true;
          }));
        }
      }
    }
    build(initial);
    const extra = node("div", undefined, "supply-property-add");
    const group = field(extra, "Property set / group (optional)");
    const key = field(extra, "New property name");
    const type = field(extra, "Value type", ["text", "number", "boolean"]);
    const value = field(extra, "Value");
    extra.append(button("Add property", () => {
      const name = key.value.trim();
      const groupName = group.value.trim();
      const path = groupName ? [groupName, name] : [name];
      const forbidden = ["__proto__", "constructor", "prototype"];
      const initialTarget = groupName ? initial[groupName] : initial;
      if (!name || forbidden.includes(name) || forbidden.includes(groupName) || controls.some((item) => item.path.join(".") === path.join(".")) || initialTarget && Object.hasOwn(initialTarget, name))
        throw new Error("Enter a unique, safe property name.");
      if (groupName && Object.hasOwn(initial, groupName) && (!initial[groupName] || typeof initial[groupName] !== "object" || Array.isArray(initial[groupName])))
        throw new Error("The selected group is not a property set object. Choose a different group name.");
      let parsed = value.value;
      if (type.value === "number") { parsed = Number(value.value); if (!value.value.trim() || !Number.isFinite(parsed)) throw new Error("Enter a finite number."); }
      if (type.value === "boolean") { if (!["true", "false"].includes(parsed)) throw new Error("Boolean values must be true or false."); parsed = parsed === "true"; }
      const input = type.value === "boolean" ? checkbox(parent, path.join(" / "), parsed) : field(parent, path.join(" / "), type.value, parsed);
      if (type.value === "number") input.removeAttribute("min");
      const entry = { path, type: type.value === "boolean" ? "checkbox" : type.value, input };
      controls.push(entry);
      parent.append(button("Remove property " + path.join(" / "), () => { entry.removed = true; input.disabled = true; input.parentElement.hidden = true; }));
      key.value = ""; value.value = "";
    }));
    parent.append(extra);
    return () => {
      const result = clone(initial);
      for (const { path, type, input, original, removed } of controls) {
        let target = result;
        for (const segment of path.slice(0, -1)) {
          if (!Object.hasOwn(target, segment)) target[segment] = {};
          target = target[segment];
        }
        if (removed) { delete target[path.at(-1)]; continue; }
        const value = type === "checkbox" ? input.checked : type === "number" ? Number(input.value) : type === "serials" ? serials(input.value) : original === null && !input.value ? null : input.value;
        if (type === "number" && (!input.value.trim() || !Number.isFinite(value))) throw new Error("Enter a finite numeric property value.");
        target[path.at(-1)] = value;
      }
      return result;
    };
  }
  async function openRecord(kind, record = null, seed = null) {
    if (!requireAccess()) return;
    if (!sc.loaded || sc.stale) await load(true);
    if (record && !isOwned(record)) throw new Error("Upstream records are read-only. Reuse a pinned source instead.");
    const requestedSeed = seed;
    if (seed && ["installation", "asset"].includes(kind)) {
      seed = candidates().filter((item) => item.authorityDid === seed.authorityDid && item.id === seed.id && item.acceptedFrom)
        .sort((left, right) => right.revision - left.revision)[0];
      if (!seed) throw new Error("No immutable accepted source revision is available. Refresh after accepting the supply.");
    }
    dialog((record ? "Edit " : "Create ") + label(kind).toLowerCase(), "1 / Data and pinned sources");
    const body = $("supply-dialog-body");
    if (requestedSeed && seed && requestedSeed.revision !== seed.revision)
      body.append(node("p", "Selected immutable accepted revision " + seed.revision + ". Working revision " + requestedSeed.revision + " has unissued changes and is not used for this allocation.", "supply-lineage"));
    const draft = { kind, record, seed, step: 1, sourceRows: [], intent: crypto.randomUUID() };
    sc.dialog = draft;
    const name = field(body, "Name", "text", record?.name || (seed ? seed.name + (kind === "offering" ? " offering" : " installation") : ""), true); name.maxLength = 160;
    const defaultClass = seed?.ifcClass || "IfcBuildingElementProxyType";
    const ifcClass = field(body, "IFC class", "text", record?.ifcClass || (kind === "installation" ? defaultClass.replace(/Type$/, "") : defaultClass), true);
    const classChoices = ["product", "offering", "supply"].includes(kind) ? sc.schema?.typeClasses : sc.schema?.occurrenceClasses;
    if (classChoices?.length) {
      const list = node("datalist"); list.id = "supply-supported-classes";
      for (const value of classChoices) { const option = node("option"); option.value = value; list.append(option); }
      ifcClass.setAttribute("list", list.id); body.append(list);
    }
    ifcClass.pattern = "Ifc[A-Za-z0-9]+";
    if (record?.acceptedFrom && kind === "supply") ifcClass.disabled = true;
    const classExamples = node("p", "Suggested presets: IfcDoorType / IfcDoor, IfcPumpType / IfcPump. Other concrete IFC4X3_ADD2 classes may be entered and are validated by the authority. Occurrence class must match the selected supply type.", "supply-field-help"); body.append(classExamples);
    let project = null;
    if (["supply", "installation", "asset"].includes(kind) || record?.projectId) {
      project = field(body, "Private local project", []);
      projectOptions(project, true, false);
      project.required = true;
      project.value = record?.projectId || seed?.projectId || $("installation-project").value || project.value;
      project.disabled = Boolean(record);
      if (!project.options.length) body.append(node("p", "Create a private local project using Projects first. Recipient projects are chosen at submission time.", "form-error"));
    }
    const dataInputs = [];
    const values = clone(record?.data || {});
    if (kind === "installation" && seed) {
      values.unit ||= seed.data.unit;
      const allocated = sc.records.filter((item) => item.kind === "installation").flatMap((item) => item.sources || [])
        .filter((pin) => pin.authorityDid === seed.authorityDid && pin.recordId === seed.id)
        .reduce((total, pin) => total + (pin.quantity || 0), 0);
      values.quantity ||= Math.max(0, (seed.data.quantity || 0) - allocated);
      values.installer ||= state.info?.did;
    }
    for (const [key, title, type = "text", required = false] of fields[kind]) {
      const input = field(body, title, type, values[key] ?? (key === "status" ? type[0] : ""), required);
      if (record?.acceptedFrom && kind === "supply" && ["quantity", "unit", "serials"].includes(key)) input.disabled = true;
      dataInputs.push({ key, input, type });
    }
    if (record?.acceptedFrom && kind === "supply") body.append(node("p", "Accepted quantity, unit, serials and IFC type are immutable upstream facts. Request a corrected supply issue rather than editing the accepted allocation.", "supply-lineage"));
    const propertySection = node("details", undefined, "supply-properties");
    propertySection.append(node("summary", "Technical / additional properties (local-authority fields only)"));
    const properties = node("div", undefined, "supply-form-body");
    const remaining = Object.fromEntries(Object.entries(values).filter(([key]) => !fields[kind].some(([field]) => key === field)));
    const getProperties = recursivePropertyEditor(properties, remaining); propertySection.append(properties); body.append(propertySection);
    const sourceSection = node("section", undefined, "supply-form-body");
    sourceSection.append(node("h3", kind === "product" ? "Component specifications" : "Source / allocation"),
      node("p", "Choose an explicit immutable revision. Changes upstream never silently replace a saved pin. Origin and intermediate suppliers remain in the lineage.", "muted"));
    const sourceList = node("div", undefined, "supply-form-body");
    sourceSection.append(sourceList); body.append(sourceSection);
    function addSource(selected = null) {
      const row = node("fieldset", undefined, "supply-source-row");
      row.append(node("legend", kind === "product" ? "Component" : "Pinned source"));
      const route = field(row, "Sourcing route", ["direct", "supplier", "accepted"], selected?.kind === "offering" ? "supplier" : ["installation", "asset"].includes(kind) ? "accepted" : "direct");
      for (const option of route.options) option.textContent = { direct: "Direct from manufacturer", supplier: "Via supplier offering", accepted: "Accepted project supply / installation" }[option.value];
      if (kind === "supply") { route.value = "supplier"; route.disabled = true; }
      if (["installation", "asset"].includes(kind)) { route.value = "accepted"; route.disabled = true; }
      const search = field(row, "Filter sources", "search", "");
      const picker = field(row, "Selected immutable revision", [], "", true);
      const preview = node("div", undefined, "supply-lineage");
      let quantity, unit, allocationSerials;
      if (kind === "product") { quantity = field(row, "Component quantity", "number", selected?.quantity || 1, true); unit = field(row, "Component unit", "text", selected?.unit || "each", true); }
      if (kind === "installation") {
        row.append(node("p", "The installed quantity, unit and serials above allocate this accepted supply. Select one accepted line per installation.", "muted"));
      }
      const entry = { row, picker, quantity, unit, allocationSerials, snapshot: selected };
      draft.sourceRows.push(entry);
      function refreshPicker(preserve = true) {
        const previous = preserve ? picker.value : "";
        picker.replaceChildren(); const blank = node("option", "Select a revision"); blank.value = ""; picker.append(blank);
        const eligible = eligibleSources(kind, candidates(), record?.id, project?.value, record?.authorityDid || state.info?.did);
        const filter = search.value.trim().toLowerCase();
        for (const item of eligible.filter((item) => (route.value === "supplier" ? item.kind === "offering" : route.value === "direct" ? item.kind === "product" : true) && (!filter || [item.name, item.ifcClass, item.authorityDid, item.data?.sku, item.data?.model].join(" ").toLowerCase().includes(filter)))) {
          const latest = Math.max(...eligible.filter((other) => other.id === item.id && other.authorityDid === item.authorityDid).map((other) => other.revision));
          const option = node("option", item.name + " / " + label(item.kind) + " / " + item.authorityDid + " / revision " + item.revision + (latest > item.revision ? " (newer revision available)" : ""));
          option.value = pinKey(item); picker.append(option);
        }
        if (selected && ![...picker.options].some((item) => item.value === pinKey(selected))) {
          const option = node("option", (selected.name || selected.recordId) + " / revision " + selected.revision + " / existing pin (detail unavailable)");
          option.value = pinKey(selected); picker.append(option);
        }
        picker.value = previous || (selected ? pinKey(selected) : "");
        preview.replaceChildren();
        const snapshot = candidates().find((item) => pinKey(item) === picker.value);
        if (snapshot) { entry.snapshot = snapshot; preview.append(lineage(snapshot), node("p", "Immediate source: " + snapshot.authorityDid + " / revision " + snapshot.revision)); }
        else if (picker.value) preview.append(node("p", "Source detail is restricted or unavailable. The server will verify this exact saved pin."));
        else preview.append(node("p", eligible.length ? "Select a source to preview its chain." : "No eligible immutable sources. Discover a catalogue or accept a supply submission and refresh."));
      }
      route.onchange = () => refreshPicker(false);
      search.oninput = () => refreshPicker();
      picker.onchange = () => {
        selected = null; refreshPicker();
        if (["installation", "offering", "supply"].includes(kind)) {
          const item = candidates().find((item) => pinKey(item) === picker.value);
          if (item) {
            if (kind === "installation") dataInputs.find((input) => input.key === "unit").input.value = item.data.unit || "";
            ifcClass.value = kind === "installation" ? item.ifcClass.replace(/Type$/, "") : item.ifcClass;
          }
        }
      };
      row.append(preview);
      if (record?.acceptedFrom) {
        route.disabled = true; search.disabled = true; picker.disabled = true;
        if (quantity) quantity.disabled = true;
        if (unit) unit.disabled = true;
        row.append(node("p", "Accepted upstream lineage is immutable. Edit recipient-owned fields or create a new onward record; do not rewrite the source chain.", "muted"));
      } else if (kind === "product") row.append(button("Remove component", () => { draft.sourceRows = draft.sourceRows.filter((item) => item !== entry); row.remove(); }));
      sourceList.append(row); refreshPicker();
      return entry;
    }
    for (const source of record?.sources || []) {
      const snapshot = candidates().find((item) => pinKey(item) === pinKey(source));
      addSource({ ...(snapshot || source), ...source });
    }
    if (!draft.sourceRows.length && seed) addSource(seed);
    if (!draft.sourceRows.length && ["offering", "supply", "installation", "asset"].includes(kind)) addSource();
    if (kind === "product" && !record?.acceptedFrom) sourceSection.append(button("Add component", () => addSource()));
    if (project) project.onchange = () => {
      for (const entry of draft.sourceRows) { entry.picker.value = ""; entry.picker.dispatchEvent(new Event("change")); }
    };
    function gather() {
      if (!$("supply-form").reportValidity()) return null;
      const className = ifcClass.value.trim();
      if (["product", "offering", "supply"].includes(kind) && !className.endsWith("Type"))
        throw new Error("Product, offering and supply records require an IFC type class ending in Type.");
      if (["installation", "asset"].includes(kind) && className.endsWith("Type"))
        throw new Error("Installations and assets require an IFC occurrence class, not a Type class.");
      const data = getProperties();
      for (const { key, input, type } of dataInputs) {
        if (type === "number") {
          if (input.value) data[key] = Number(input.value);
        } else if (type === "serials") data[key] = serials(input.value);
        else if (input.value.trim()) data[key] = input.value.trim();
      }
      const sources = record?.acceptedFrom ? clone(record.sources) : draft.sourceRows.map((entry) => {
        if (!entry.picker.value) throw new Error("Select an immutable source revision.");
        const [authorityDid, recordId, revision] = JSON.parse(entry.picker.value);
        const source = { authorityDid, recordId, revision };
        if (kind === "product") { source.quantity = Number(entry.quantity.value); source.unit = entry.unit.value.trim(); }
        if (kind === "installation") { source.quantity = data.quantity; source.unit = data.unit; source.serials = data.serials || []; }
        return source;
      });
      if (kind === "installation" && sources.length !== 1)
        throw new Error("Allocate one accepted supply line per installation.");
      if (["offering", "supply"].includes(kind) && sources.length !== 1)
        throw new Error("Select exactly one upstream source for an offering or supply.");
      if (kind === "supply") assertAllocation(data, [], [], [], record?.id);
      if (kind === "installation") assertAllocation(data, sources, candidates(), sc.records, record?.id);
      return { kind, name: name.value.trim(), ifcClass: ifcClass.value.trim(), projectId: project?.value || null, data, sources, ...(record ? { expectedRevision: record.revision } : {}) };
    }
    const editBody = [...body.childNodes];
    $("supply-dialog-next").hidden = false;
    $("supply-dialog-next").onclick = async () => {
      const next = $("supply-dialog-next");
      next.disabled = true;
      try {
        const payload = gather(); if (!payload) return;
        if (payload.sources.length) {
          const preview = await api(base + "/dependencies/preview", "POST", { sources: payload.sources });
          if (sc.dialog !== draft || !$("supply-dialog").open) return;
          const verified = items(preview, "Dependency preview");
          if (!Array.isArray(preview.digests) || verified.length !== payload.sources.length || preview.digests.length !== verified.length)
            throw new Error("Dependency preview returned an invalid pin manifest.");
          payload.sources = payload.sources.map((source, index) => {
            const snapshot = verified[index];
            if (pinKey(snapshot) !== pinKey(source)) throw new Error("Dependency preview does not match the selected revision.");
            return { ...source, digest: preview.digests[index], datasetId: snapshot.datasetId, entityPath: snapshot.graphPath };
          });
          draft.dependencies = verified;
        }
        draft.payload = payload; draft.step = 2;
        body.replaceChildren(node("p", "This saves a private working record controlled by " + state.info.did + ". Upstream facts are referenced, not edited.", "supply-lineage"));
        showValues(body, { name: payload.name, kind: label(kind), ifcClass: payload.ifcClass, project: payload.projectId || "Private organisational workspace", data: payload.data });
        for (const source of payload.sources) showValues(body, { pinnedSource: source });
        for (const dependency of draft.dependencies || []) readonlySourceFacts(body, dependency);
        $("supply-dialog-step").textContent = "2 / Review private record";
        $("supply-dialog-back").hidden = false; $("supply-dialog-next").hidden = true; $("supply-dialog-submit").hidden = false;
      } catch (error) { showError(error); }
      finally { if (sc.dialog === draft) next.disabled = false; }
    };
    $("supply-dialog-back").onclick = () => {
      body.replaceChildren(...editBody); draft.step = 1;
      $("supply-dialog-step").textContent = "1 / Data and pinned sources";
      $("supply-dialog-back").hidden = true; $("supply-dialog-next").hidden = false; $("supply-dialog-submit").hidden = true;
    };
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (draft.step !== 2) throw new Error("Review the record before saving.");
      const payload = { ...draft.payload, idempotencyKey: operationKey("record:" + (record?.id || "new") + ":" + draft.intent, draft.payload) };
      await api(base + "/records" + (record ? "/" + encodeURIComponent(record.id) : ""), record ? "PUT" : "POST", payload);
      $("supply-dialog").close(); await load(true); notice("Private record saved. Existing immutable revisions and downstream pins are unchanged.");
    });
  }
  async function commitForm(event, action) {
    event.preventDefault();
    const submit = $("supply-dialog-submit");
    if (submit.disabled) return;
    submit.disabled = true; $("supply-dialog").dataset.saving = "true"; $("supply-dialog-error").replaceChildren();
    const close = $("supply-dialog").querySelector('[data-close="supply-dialog"]');
    if (close) close.disabled = true;
    try { await action(); } catch (error) { showError(error); }
    finally { submit.disabled = false; $("supply-dialog").dataset.saving = "false"; if (close) close.disabled = false; }
  }
  async function openRecordDetail(record, publicOnly = false) {
    dialog(record.name, publicOnly ? "Public immutable revision / read-only" : "Local authority record / private working data");
    const body = $("supply-dialog-body");
    body.append(node("p", technicalStatus(record), "supply-lineage"));
    showValues(body, { authority: record.authorityDid, recordId: record.id, revision: record.revision, state: record.status, ifcClass: record.ifcClass, project: record.projectId || "Organisation workspace", data: record.data });
    body.append(lineage(record));
    for (const source of record.dependencies || record.sources?.map((pin) => candidates().find((item) => pinKey(item) === pinKey(pin))).filter(Boolean) || [])
      readonlySourceFacts(body, source);
    if (record.acceptedFrom) {
      const original = node("details"); original.append(node("summary", "Original accepted source facts / read-only"));
      showValues(original, record.acceptedFrom.snapshot.data);
      body.append(original);
    }
    body.append(node("h3", "Versioned documentation"));
    for (const document of record.documents || []) {
      body.append(documentDescription(document), button("Read " + document.name, () => document.content ? downloadPublicDocument(document) : downloadDocument(record, document)));
      if (!publicOnly && document.authorityDid === state.info?.did) body.append(button("Recipients / expiry / revoke", () => openDocumentGrants(record, document)));
      if (!publicOnly && canManageAttachment(record, document)) body.append(button("Retire attachment from next revision", () => openRetireDocument(record, document)));
    }
    if (!record.documents?.length) body.append(node("p", "No documents recorded.", "muted"));
    if (publicOnly) return;
    const history = items(await api(base + "/records/" + encodeURIComponent(record.id) + "/revisions"), "Revision history");
    body.append(node("h3", "Immutable history / comparison"));
    if (!history.length) body.append(node("p", "No immutable issued revisions yet. This working record is not a publication.", "muted"));
    for (const revision of history) {
      const block = node("details"); block.append(node("summary", "Revision " + revision.revision + " / " + revision.status));
      showValues(block, { name: revision.name, data: revision.data, sources: revision.sources });
      const changes = compareData(revision.data, record.data);
      block.append(node("p", changes.length ? "Compared with current working data: " + changes.join("; ") : "No data changes from current working record.", "muted"));
      body.append(block);
    }
  }
  function compareData(before, after, path = "") {
    const result = [];
    for (const key of new Set([...Object.keys(before || {}), ...Object.keys(after || {})])) {
      const left = before?.[key], right = after?.[key], name = path + key;
      if (JSON.stringify(left) === JSON.stringify(right)) continue;
      if (left && right && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) result.push(...compareData(left, right, name + "."));
      else result.push(name + ": " + String(left ?? "(not set)") + " -> " + String(right ?? "(removed)"));
    }
    return result;
  }
  function readonlySourceFacts(parent, record) {
    const details = node("details", undefined, "supply-properties");
    details.append(node("summary", record.name + " / " + record.authorityDid + " / immutable revision " + record.revision + " / read-only source facts"));
    showValues(details, { ifcClass: record.ifcClass, data: record.data });
    details.append(lineage(record));
    for (const document of record.documents || []) {
      details.append(node("p", document.name + " / " + document.visibility + " / version " + document.id, "muted"));
      if (document.content) details.append(button("Read public " + document.name, () => downloadPublicDocument(document)));
    }
    parent.append(details);
  }
  function openPublish(record) {
    if (!requireAccess()) return;
    dialog("Publish " + record.name, "Public disclosure preview", "Publish immutable public revision");
    sc.dialog = { record, mode: "publish" };
    const body = $("supply-dialog-body");
    body.append(node("p", "Public scope: all product / offering data shown below, its pinned dependency closure, and bytes of documents explicitly marked public. Project records cannot be published. Downloads cannot be recalled.", "supply-lineage"));
    showValues(body, { name: record.name, authority: record.authorityDid, revision: record.revision, ifcClass: record.ifcClass, data: record.data });
    body.append(lineage(record));
    const previous = sc.revisions.filter((item) => item.id === record.id && item.revision < record.revision).at(-1);
    if (previous) {
      const changes = compareData(previous.data, record.data);
      body.append(node("p", "Changes since revision " + previous.revision + ": " + (changes.join("; ") || "No product data changes. Review source and document versions below."), "supply-lineage"));
    }
    for (const document of record.documents || []) body.append(node("p", document.name + " / " + document.visibility + (document.visibility === "public" ? " / bytes included" : " / omitted")));
    const confirmation = checkbox(body, "I have reviewed every field and dependency; no project prices, client details, private locations or serial allocations are disclosed.");
    confirmation.required = true;
    $("supply-dialog-submit").hidden = false;
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (!confirmation.checked) throw new Error("Confirm the public disclosure scope.");
      await api(base + "/records/" + encodeURIComponent(record.id) + "/publish", "POST", { expectedRevision: record.revision });
      $("supply-dialog").close(); await load(true); notice("Public immutable revision published. Previously selected versions remain pinned.");
    });
  }
  function openFreeze(record) {
    if (!requireAccess()) return;
    dialog("Freeze private revision of " + record.name, "Private immutable version / not publication or delivery", "Freeze private revision");
    sc.dialog = { record, mode: "freeze" };
    const body = $("supply-dialog-body");
    body.append(node("p", "This preserves a signed immutable version at revision " + record.revision + " for explicit source selection or later issue. It does not publish, deliver, accept or install the record. Further edits create a new working revision; this frozen version cannot later be made public without an edit.", "supply-lineage"));
    showValues(body, { name: record.name, revision: record.revision, ifcClass: record.ifcClass, data: record.data });
    body.append(lineage(record));
    $("supply-dialog-submit").hidden = false;
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      await api(base + "/records/" + encodeURIComponent(record.id) + "/revisions", "POST", { expectedRevision: record.revision });
      $("supply-dialog").close(); await load(true); notice("Private immutable revision frozen. No publication, delivery or acceptance occurred.");
    });
  }
  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("Unable to read the selected file."));
      reader.onabort = () => reject(new Error("Document reading was cancelled."));
      reader.readAsDataURL(file);
    });
  }
  function openDocument(record) {
    if (!requireAccess()) return;
    dialog("Attach document to " + record.name, "New document version", "Upload document");
    sc.dialog = { record, mode: "document" };
    const body = $("supply-dialog-body");
    const intent = crypto.randomUUID();
    const file = field(body, "Document file", "file", "", true);
    const name = field(body, "Document name", "text", "", true);
    const mediaType = field(body, "Media type", "text", "application/pdf", true);
    const visibility = field(body, "Document visibility", ["private", ...(["product", "offering"].includes(record.kind) && !record.projectId ? ["public"] : [])], "private");
    const replacement = field(body, "Replace existing document (optional)", []);
    const none = node("option", "Add a separate attachment"); none.value = ""; replacement.append(none);
    for (const document of record.documents || []) if (canManageAttachment(record, document)) {
      const option = node("option", document.name + " / version " + document.id); option.value = document.id; replacement.append(option);
    }
    file.onchange = () => { if (file.files[0]) { name.value = file.files[0].name; mediaType.value = file.files[0].type || "application/octet-stream"; } };
    body.append(node("p", "Uploading creates a new document ID and record revision. Replacing an owned attachment removes its current reference only; historical frozen revisions and issued submissions remain unchanged, old bytes are not deleted and existing read grants are unaffected. Public bytes enter the catalogue only when you explicitly publish. New-version grants are separate; no retention duration is promised by this service.", "supply-lineage"));
    $("supply-dialog-submit").hidden = false;
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (!$("supply-form").reportValidity()) return;
      const selected = file.files[0]; if (!selected?.size) throw new Error("Select a non-empty document.");
      const content = await readFile(selected);
      const payload = { name: name.value.trim(), mediaType: mediaType.value.trim(), content, visibility: visibility.value, expectedRevision: record.revision };
      if (replacement.value) payload.replacesDocumentId = replacement.value;
      await api(base + "/records/" + encodeURIComponent(record.id) + "/documents", "POST", { ...payload, idempotencyKey: operationKey("document:" + record.id + ":" + intent, payload) });
      $("supply-dialog").close(); await load(true); notice("Document attached as a new version; republish or issue a new submission to disclose it.");
    });
  }
  function canManageAttachment(record, document) {
    return isOwned(record) && document.authorityDid === record.authorityDid && document.recordId === record.id;
  }
  function openRetireDocument(record, document) {
    if (!requireAccess()) return;
    if (!canManageAttachment(record, document)) throw new Error("Only an original locally owned attachment can be retired. Upstream evidence is read-only.");
    dialog("Retire attachment: " + document.name, "Current reference only / immutable history preserved", "Retire from next revision");
    sc.dialog = { record, mode: "retire", document };
    const body = $("supply-dialog-body");
    body.append(documentDescription(document), node("p", "Retiring removes this attachment from the next working revision. Frozen publications and issued submissions remain unchanged; stored bytes are not deleted and existing access grants are unaffected. Use the separate grant controls to revoke future access when needed.", "supply-lineage"));
    const confirmation = checkbox(body, "Retire this exact current attachment; keep historical issues and existing grants unchanged.");
    confirmation.required = true; $("supply-dialog-submit").hidden = false;
    const intent = crypto.randomUUID();
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (!confirmation.checked) throw new Error("Confirm attachment retirement.");
      const payload = { expectedRevision: record.revision };
      await api(base + "/records/" + encodeURIComponent(record.id) + "/documents/" + encodeURIComponent(document.id) + "/detach", "POST", { ...payload, idempotencyKey: operationKey("detach:" + intent, payload) });
      $("supply-dialog").close(); await load(true); notice("Attachment retired from the working record. Historical revisions and read grants are unchanged.");
    });
  }
  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob); const link = node("a");
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function downloadDocument(record, document) {
    if (!requireAccess()) return;
    await downloadBinary(base + "/records/" + encodeURIComponent(record.id) + "/documents/" + encodeURIComponent(document.id), document.name);
  }
  async function downloadSourceDocument(document, projectId) {
    if (document.content) { downloadPublicDocument(document); return; }
    if (!requireAccess()) return;
    const query = new URLSearchParams({ authorityDid: document.authorityDid });
    if (projectId) query.set("projectId", projectId);
    await downloadBinary(base + "/documents/" + encodeURIComponent(document.id) + "?" + query, document.name);
  }
  async function downloadBinary(path, name) {
    const headers = {};
    if ($("key").value) headers["x-api-key"] = $("key").value;
    const response = await fetch(path, { headers });
    if (!response.ok) {
      const text = await response.text(); let message = text;
      try { const value = JSON.parse(text); message = typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail); } catch { message = "Document read failed (" + response.status + ")."; }
      const error = new Error(message); error.status = response.status; throw error;
    }
    saveBlob(await response.blob(), name);
  }
  function downloadPublicDocument(document) {
    const bytes = Uint8Array.from(atob(document.content), (character) => character.charCodeAt(0));
    saveBlob(new Blob([bytes], { type: document.mediaType }), document.name);
  }
  function operationKey(action, body) {
    const key = action + ":" + JSON.stringify(body);
    if (!sc.keys.has(key)) sc.keys.set(key, crypto.randomUUID());
    return sc.keys.get(key);
  }
  function deliveryRequest(action, id, payload) {
    const key = action + ":" + id;
    if (!sc.deliveryRequests.has(key))
      sc.deliveryRequests.set(key, { ...clone(payload), idempotencyKey: operationKey(key, payload) });
    return sc.deliveryRequests.get(key);
  }
  async function sendDelivery(action, id, payload) {
    try { return await api(base + "/submissions/" + encodeURIComponent(id) + "/" + action, "POST", payload); }
    catch (error) {
      if (error.status && error.status !== 424) sc.deliveryRequests.delete(action + ":" + id);
      throw error;
    }
  }
  async function openDocumentGrants(record, document) {
    if (!requireAccess()) return;
    const list = items(await api(base + "/records/" + encodeURIComponent(record.id) + "/documents/" + encodeURIComponent(document.id) + "/grants"), "Document grants");
    dialog("Recipients for " + document.name, "Source-authorised evidence access", "Save grant");
    sc.dialog = { grant: { record, document } };
    const body = $("supply-dialog-body");
    body.append(documentDescription(document), node("p", "Only the original document authority can grant or revoke access. Expiry stops future reads, not copies already downloaded; it is not a byte-retention promise.", "supply-lineage"));
    const recipient = field(body, "Recipient organisation DID", "text", "", true); recipient.pattern = "did:web:.+";
    const active = checkbox(body, "Allow future authenticated reads", true);
    const expiry = field(body, "Grant expiry (optional, local time)", "datetime-local");
    let expectedRevision = 0;
    function setGrant() {
      const grant = list.find((item) => item.recipientDid === recipient.value.trim());
      expectedRevision = grant?.revision || 0; active.checked = grant?.active ?? true;
      if (grant?.expiresAt) {
        const date = new Date(grant.expiresAt);
        expiry.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      } else expiry.value = "";
    }
    recipient.onchange = setGrant;
    for (const grant of list) body.append(button(grant.recipientDid + " / " + (grant.active ? "active" : "revoked") + " / revision " + grant.revision + " / expiry " + (grant.expiresAt || "not set"), () => { recipient.value = grant.recipientDid; setGrant(); }));
    $("supply-dialog-submit").hidden = false;
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (!$("supply-form").reportValidity()) return;
      const expiresAt = expiry.value ? new Date(expiry.value).toISOString() : null;
      if (expiresAt && new Date(expiresAt) <= new Date()) throw new Error("Grant expiry must be in the future.");
      await api(base + "/records/" + encodeURIComponent(record.id) + "/documents/" + encodeURIComponent(document.id) + "/grants", "POST", { recipientDid: recipient.value.trim(), active: active.checked, expectedRevision, expiresAt });
      await openDocumentGrants(record, document);
    });
  }
  function openSubmission(seed = null, prior = null) {
    if (!requireAccess()) return;
    const correction = prior && prior.status !== "draft" ? prior : null;
    dialog(correction ? "Prepare corrected issue" : prior ? "Prepare fresh reviewed draft" : "Prepare submission", "1 / Recipient, records and document grants");
    const body = $("supply-dialog-body");
    const destination = field(body, "Recipient project destination", [], "", true); projectOptions(destination, false, false, true);
    if (prior) destination.value = JSON.stringify([prior.recipientDid, prior.projectId]);
    const destinationInfo = node("p", undefined, "supply-lineage"); body.append(destinationInfo);
    const updateDestination = () => {
      const item = sc.projects.find((project) => projectKey(project) === destination.value);
      destinationInfo.textContent = item ? "Recipient: " + item.authorityDid + " / project: " + item.projectId + " / " + (item.local ? "local" : "linked remote project") + ". No local API key is sent to the recipient." : "Connect to a remote project or create a private local project first.";
    };
    destination.onchange = updateDestination; updateDestination();
    const recordSection = node("fieldset", undefined, "supply-form-body"); recordSection.append(node("legend", "Select owned records (maximum 32)")); body.append(recordSection);
    const selectedRecords = sc.records.map((record) => ({ record, input: checkbox(recordSection, record.name + " / " + label(record.kind) + " / working revision " + record.revision, record.id === seed?.id || prior?.recordIds.includes(record.id)) }));
    const documentSection = node("fieldset", undefined, "supply-form-body"); documentSection.append(node("legend", "Explicit document read grants")); body.append(documentSection);
    const selectedDocuments = [];
    function updateDocuments() {
      const previous = new Set(selectedDocuments.filter((item) => item.input.checked).map((item) => item.document.id));
      selectedDocuments.length = 0; documentSection.replaceChildren(node("legend", "Explicit document read grants"));
      for (const { record, input } of selectedRecords) if (input.checked) for (const document of record.documents || []) {
        const control = checkbox(documentSection, document.name + " / " + document.visibility + " / issuer " + document.authorityDid, previous.has(document.id) || prior?.documentIds.includes(document.id));
        if (document.authorityDid !== state.info?.did || document.recordId && document.recordId !== record.id) {
          control.checked = false; control.disabled = true;
          documentSection.append(node("p", "Source-owned document: " + document.name + ". No new byte grant can be issued by this organisation; its original authority must grant the recipient access separately. Metadata remains in the lineage.", "form-error"));
        }
        selectedDocuments.push({ document, input: control });
      }
      if (!selectedDocuments.length) documentSection.append(node("p", "No attached documents on the selected records.", "muted"));
    }
    for (const item of selectedRecords) item.input.onchange = updateDocuments;
    updateDocuments();
    body.append(node("p", "Issuing freezes the selected records and dependencies, or reuses an existing immutable revision at the same version. Document metadata in that revision travels in the snapshot; checked documents add explicit read grants. Upstream document permissions are not broadened. Missing evidence or source grants block issue / retrieval.", "supply-lineage"));
    if (correction) body.append(node("p", "New issue will supersede " + correction.id + ". Its signed contents and earlier decisions remain unchanged.", "supply-lineage"));
    const draft = { prior, step: 1, intent: crypto.randomUUID() }; sc.dialog = draft;
    const editBody = [...body.childNodes];
    $("supply-dialog-next").hidden = false;
    $("supply-dialog-next").onclick = () => {
      try {
        if (!$("supply-form").reportValidity()) return;
        const target = sc.projects.find((item) => projectKey(item) === destination.value);
        if (!target || !destinationEligible(target)) throw new Error("Select a known local or accepted linked recipient project. Pending invitations are not submission destinations.");
        const recordIds = selectedRecords.filter((item) => item.input.checked).map((item) => item.record.id);
        if (!recordIds.length || recordIds.length > 32) throw new Error("Select between 1 and 32 records.");
        const documentIds = selectedDocuments.filter((item) => item.input.checked).map((item) => item.document.id);
        draft.payload = { recipientDid: target.authorityDid, projectId: target.projectId, recordIds, documentIds, supersedes: correction?.id || null };
        draft.step = 2;
        body.replaceChildren(node("h3", "Submission disclosure preview"));
        showValues(body, { recipient: target.authorityDid, project: target.name + " / " + target.projectId, supersedes: correction?.id || "None" });
        for (const item of selectedRecords.filter((item) => item.input.checked)) {
          const preview = issuePreview(item.record);
          body.append(node("h3", preview.name + " / selected revision " + preview.revision));
          showValues(body, preview.data); body.append(lineage(preview));
        }
        for (const item of selectedDocuments) {
          const record = selectedRecords.find((selected) => selected.record.documents?.some((document) => document.id === item.document.id))?.record;
          const included = record && issuePreview(record).documents?.some((document) => document.id === item.document.id);
          body.append(node("p", item.document.name + " / " + (included || item.input.checked ? "metadata included" : "omitted from existing immutable revision") + " / " + (item.input.checked ? "recipient read grant selected" : "no new byte grant")));
        }
        body.append(node("p", "Save creates a private draft only. Review and issue it separately; no delivery or acceptance is implied.", "supply-lineage"));
        $("supply-dialog-step").textContent = "2 / Disclosure preview";
        $("supply-dialog-back").hidden = false; $("supply-dialog-next").hidden = true; $("supply-dialog-submit").hidden = false; $("supply-dialog-submit").textContent = "Save submission draft";
      } catch (error) { showError(error); }
    };
    $("supply-dialog-back").onclick = () => {
      body.replaceChildren(...editBody); draft.step = 1;
      $("supply-dialog-back").hidden = true; $("supply-dialog-next").hidden = false; $("supply-dialog-submit").hidden = true;
    };
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (draft.step !== 2) throw new Error("Review the disclosure scope before saving.");
      const result = await api(base + "/submissions", "POST", { ...draft.payload, idempotencyKey: operationKey("create-submission:" + draft.intent, draft.payload) });
      await load(true); await openSubmissionDetail(result);
    });
  }
  async function openSubmissionDetail(submission) {
    if (!submission) throw new Error("Submission no longer available. Refresh the inbox/outbox.");
    const current = await api(base + "/submissions/" + encodeURIComponent(submission.id));
    const records = current.issue?.records || await Promise.all(current.recordIds.map((id) => api(base + "/records/" + encodeURIComponent(id))));
    if (records.some((item) => !item?.id || !Number.isInteger(item.revision))) throw new Error("Submission record preview is invalid or unavailable. Refresh before issuing.");
    if (current.status === "draft") {
      const fresh = new Map(records.map((item) => [item.id, item]));
      sc.records = sc.records.map((item) => fresh.get(item.id) || item);
    }
    const changed = current.status === "draft" && current.recordRevisions
      ? records.filter((item) => item.revision !== current.recordRevisions[item.id]) : [];
    dialog("Submission " + current.id, readable(current.status) + " / " + (current.direction === "incoming" ? "Incoming review" : "Outgoing issue"));
    sc.dialog = { submission: current };
    const body = $("supply-dialog-body");
    showValues(body, { sender: current.senderDid, recipient: current.recipientDid, destinationProject: current.projectId, revision: current.revision, workflowState: current.status, delivery: current.deliveryStatus || "received / not applicable", supersedes: current.supersedes || "None" });
    if (current.status === "draft") {
      body.append(node("p", "Draft captures the selected working revisions. Issue freezes exactly those versions; changed records require a new reviewed draft.", "supply-lineage"));
      if (changed.length) body.append(node("p", "Cannot issue: " + changed.map((item) => item.name + " changed from revision " + current.recordRevisions[item.id] + " to " + item.revision).join("; "), "form-error"),
        button("Create fresh reviewed draft", () => openSubmission(null, current)));
    }
    for (const working of records) {
      const record = current.issue ? working : issuePreview(working);
      body.append(node("h3", record.name + " / " + label(record.kind) + " / revision " + record.revision + (current.issue ? " / immutable" : " / working draft")));
      showValues(body, { ifcClass: record.ifcClass, data: record.data }); body.append(lineage(record));
      const documents = new Map((record.documents || []).map((document) => [document.id, document]));
      for (const document of current.issue?.documents || working.documents?.filter((document) => current.documentIds.includes(document.id)) || [])
        if (document.recordId === record.id || !document.recordId && current.recordIds.length === 1) documents.set(document.id, document);
      for (const document of documents.values()) {
        body.append(documentDescription(document), node("p", current.documentIds.includes(document.id) ? "Recipient read grant selected" : "Metadata only / no new byte grant"));
        body.append(button("Check access / read " + document.name, () => current.issue && current.documentIds.includes(document.id)
          ? downloadBinary(base + "/submissions/" + encodeURIComponent(current.id) + "/documents/" + encodeURIComponent(document.id), document.name)
          : downloadSourceDocument(document, current.projectId)));
      }
    }
    if (current.issue?.created) body.append(node("p", "Issued " + new Date(current.issue.created).toLocaleString() + " / signed immutable issue verified by the local broker.", "muted"));
    if (current.supersedes) {
      const previous = sc.submissions.find((item) => item.id === current.supersedes);
      if (previous?.issue) for (const record of records) {
        const old = previous.issue.records.find((item) => item.id === record.id);
        if (old) { const changes = compareData(old.data, record.data); body.append(node("p", "Changes to " + record.name + ": " + (changes.join("; ") || "No data changes"), "supply-lineage")); }
      }
      else body.append(node("p", "Earlier issue is unavailable for comparison; its ID remains bound to this correction.", "muted"));
    }
    if (current.decision) {
      body.append(node("h3", "Recipient decision"));
      showValues(body, { decision: current.decision.decision, reason: current.decision.reason || "No reason recorded", created: current.decision.created, selectedRecords: current.decision.recordIds });
      body.append(node("p", "Acceptance is a recorded business decision, not technical certification, delivery installation or completion.", "supply-lineage"));
      if (current.decisionDeliveryStatus === "pending") body.append(button("Retry decision delivery", async () => {
        const payload = sc.deliveryRequests.get("decision:" + current.id);
        if (!payload) throw new Error("The exact failed decision request is not retained in this browser session. Do not create a new decision; retry the original request through the operator API.");
        await sendDelivery("decision", current.id, payload);
        await load(true); await openSubmissionDetail(current);
      }));
    }
    const outgoing = current.direction === "outgoing";
    if (outgoing && !current.decision && !changed.length && ["draft", "issued"].includes(current.status)) {
      const confirmation = checkbox(body, "I reviewed the recipient, selected records, exact revision scope and evidence access. Issue / retry this immutable submission.");
      body.append(button(current.status === "draft" ? "Issue immutable submission" : "Retry delivery of the same issue", async () => {
        if (!confirmation.checked) throw new Error("Confirm the issue scope before sending.");
        const payload = deliveryRequest("issue", current.id, { expectedRevision: current.revision });
        await sendDelivery("issue", current.id, payload);
        await load(true); await openSubmissionDetail(current);
      }, ""));
    }
    const incoming = current.direction === "incoming" || current.localRecipient;
    if (incoming && current.issue && !current.decision) {
      body.append(node("h3", "Review the whole submission"));
      const reason = field(body, "Decision reason / requested correction", "textarea", ""); reason.maxLength = 2000;
      const confirmation = checkbox(body, "I reviewed the pinned records, lineage and technical status and checked required evidence access. Acceptance is not technical completion.");
      for (const [decision, title] of [["accept", "Accept selected issue"], ["reject", "Reject with reason"], ["request-changes", "Request changes"]]) body.append(button(title, async () => {
        if (decision === "accept" && !confirmation.checked) throw new Error("Review the evidence and technical status before accepting.");
        if (decision !== "accept" && !reason.value.trim()) throw new Error("Give a reason for rejecting or requesting changes.");
        const payload = deliveryRequest("decision", current.id, { decision, reason: reason.value.trim(), expectedRevision: current.revision });
        if (payload.decision !== decision || payload.reason !== reason.value.trim())
          throw new Error("A decision request is already pending delivery. Retry its original decision and reason; do not replace a persisted decision.");
        await sendDelivery("decision", current.id, payload);
        await load(true); await openSubmissionDetail(current);
      }, decision === "accept" ? "" : "secondary"));
      body.append(node("p", "This decision covers all issued records. Accepting supply creates recipient-owned supply records for later installation; it never marks delivery as installed.", "supply-lineage"));
    }
    if (outgoing && current.status !== "draft") body.append(button("Prepare corrected / onward issue", () => openSubmission(null, current)));
    for (const accepted of current.acceptedRecords || []) {
      body.append(node("h3", "Recipient-owned " + label(accepted.kind) + ": " + accepted.name));
      if (accepted.kind === "supply") body.append(button("Record installation from accepted supply", () => openRecord("installation", null, sc.records.find((item) => item.id === accepted.id) || accepted)));
      else body.append(button("Submit onward", () => openSubmission(sc.records.find((item) => item.id === accepted.id) || accepted)));
    }
  }
  function openConnect() {
    if (!requireAccess()) return;
    dialog("Link a recipient project", "Project access, not product acceptance", "Connect project");
    const body = $("supply-dialog-body");
    const did = field(body, "Recipient organisation DID", "text", "", true); did.pattern = "did:web:.+";
    const project = field(body, "Recipient project ID", "text", "", true);
    body.append(node("p", "The recipient must already approve this organisation either as a scoped submission sender or a project member. Sender approval does not grant graph reads, Contributor rights or proposal trust. This broker verifies eligibility and saves a destination; it does not join a project or accept products.", "supply-lineage"));
    $("supply-dialog-submit").hidden = false;
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      if (!$("supply-form").reportValidity()) return;
      await api(base + "/projects/connect", "POST", { authorityDid: did.value.trim(), projectId: project.value.trim() });
      $("supply-dialog").close(); await load(true); notice("Project destination linked.");
    });
  }
  function initialize() {
    const nav = $("tab-products").parentElement;
    $("supply-dialog").addEventListener("cancel", (event) => { if ($("supply-dialog").dataset.saving === "true") event.preventDefault(); });
    $("supply-dialog").addEventListener("close", () => { sc.dialog = null; });
    for (const id of ["products", "supplied", "discover", "projects", "installations", "submissions", "documents", "assets", "overview", "activity", "replication"]) nav.append($("tab-" + id));
    $("tab-overview").lastChild.textContent = "Network operations";
    const discovery = node("div", undefined, "supply-filter");
    const did = field(discovery, "Discover catalogue by organisation DID", "text", "");
    did.placeholder = "did:web:manufacturer.example"; did.pattern = "did:web:.+";
    discovery.append(button("Discover verified revisions", async () => {
      if (!requireAccess()) return;
      if (!did.value.trim().startsWith("did:web:")) throw new Error("Enter an organisation did:web identity.");
      const response = await api(base + "/catalogue/discover", "POST", { authorityDid: did.value.trim() });
      sc.discovered.set(response.authorityDid, items(response, "Partner catalogue")); renderCatalogue();
      $("catalogue-status").textContent = "Verified catalogue from " + response.authorityDid + ". Choose a specific immutable revision; this is not a global network search.";
    }));
    $("catalogue-list").before(discovery);
    const projectsTools = $("new-project").parentElement;
    projectsTools.append(button("Link remote destination", openConnect));
    const destinationHeading = node("h2", "Linked remote project destinations", "supply-subheading");
    const destinationList = node("div", undefined, "supply-record-list"); destinationList.id = "supply-destinations";
    $("projects").append(destinationHeading, destinationList);
    $("create-product").onclick = () => run(() => openRecord("product"));
    $("manage-native-product").onclick = () => run(openAdopt);
    $("create-offering").onclick = () => run(() => openRecord("offering"));
    $("create-supply").onclick = () => run(() => openRecord("supply"));
    $("create-installation").onclick = () => run(() => openRecord("installation"));
    $("create-submission").onclick = () => run(() => openSubmission());
    $("refresh-catalogue").onclick = () => run(() => load(true));
    $("catalogue-kind").onchange = renderCatalogue; $("catalogue-search").oninput = renderCatalogue;
    $("installation-project").onchange = renderInstallations;
    for (const id of ["submission-direction", "submission-project", "submission-status-filter"]) $(id).onchange = renderSubmissions;
    for (const id of ["products", "supplied", "installations", "submissions", "documents"]) {
      const toolbar = $(id).querySelector(".page-heading");
      toolbar.append(button("Refresh", () => load(true)));
    }
    window.addEventListener("clip:dashboard-refresh", () => {
      sc.stale = true;
      run(() => load(true));
      if (state.request === 1) {
        const view = state.info?.role === "manufacturer" ? "products" : state.info?.role === "supplier" ? "supplied" : ["contractor", "main_contractor", "owner", "client"].includes(state.info?.role) ? "installations" : "overview";
        setTimeout(() => activateView(view), 0);
      }
    });
    window.addEventListener("clip:dashboard-view", (event) => {
      if (views.has(event.detail.view) && (!sc.loaded || sc.stale)) run(() => load());
    });
    status("Choose a workspace or refresh to load supply-chain data.");
    renderSupply();
  }
  async function openAdopt() {
    if (!requireAccess()) return;
    const response = await api(base + "/records/adoption-candidates");
    const candidates = items(response, "Native type candidates");
    if (response.authorityDid !== state.info?.did || !Number.isInteger(response.sequence) || response.sequence < 0 ||
      candidates.some((item) => item.visibility !== "private" || !item.datasetId || !item.entityPath))
      throw new Error("Native type candidate response has invalid authority, sequence or private ownership.");
    dialog("Manage existing product type", "Private local native IFC type / no imported records", "Adopt existing type");
    sc.dialog = { adopting: true };
    const body = $("supply-dialog-body");
    body.append(node("p", "Only locally authored product types in private datasets are listed. Adoption preserves the existing dataset and entity path and makes its properties available to this guided workflow; it does not copy or edit imported types. Public libraries cannot acquire private workflow metadata: create a private-backed type separately rather than changing public access automatically.", "supply-lineage"));
    const dataset = field(body, "Local private dataset / library", [], "", true);
    for (const id of new Set(candidates.map((item) => item.datasetId))) {
      const candidate = candidates.find((item) => item.datasetId === id);
      const option = node("option", candidate.projectName + " / " + id); option.value = id; dataset.append(option);
    }
    const entity = field(body, "Existing native product type", [], "", true);
    const preview = node("div", undefined, "supply-form-body"); body.append(preview);
    function previewEntity() {
      const item = candidates.find((candidate) => candidate.datasetId === dataset.value && candidate.entityPath === entity.value);
      preview.replaceChildren();
      if (item) showValues(preview, { name: item.name, ifcClass: item.ifcClass, datasetId: item.datasetId, entityPath: item.entityPath, properties: item.properties });
    }
    function selectDataset() {
      entity.replaceChildren();
      for (const item of candidates.filter((candidate) => candidate.datasetId === dataset.value)) {
        const option = node("option", item.name + " / " + item.ifcClass + " / " + item.entityPath);
        option.value = item.entityPath; entity.append(option);
      }
      previewEntity();
    }
    dataset.onchange = selectDataset; entity.onchange = previewEntity; selectDataset();
    if (!candidates.length) body.append(node("p", "No unmanaged private native product types. Existing public libraries and imported types are intentionally excluded. Use Projects / Create entity in a private product library, or create a new private product here.", "muted"));
    const confirm = checkbox(body, "Manage this exact existing native type; preserve its dataset, path and original properties.");
    confirm.required = true;
    $("supply-dialog-submit").hidden = false; $("supply-dialog-submit").disabled = !candidates.length;
    const intent = crypto.randomUUID();
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      const selected = candidates.find((item) => item.datasetId === dataset.value && item.entityPath === entity.value);
      if (!selected || !confirm.checked) throw new Error("Select and confirm a verified private native product type.");
      const payload = { datasetId: selected.datasetId, entityPath: selected.entityPath, kind: "product", expectedSequence: response.sequence };
      const record = await api(base + "/records/adopt", "POST", { ...payload, idempotencyKey: operationKey("adopt:" + intent, payload) });
      await load(true); await openRecord("product", record);
    });
  }
  async function openSenders(project) {
    if (!requireAccess()) return;
    const policy = await api(base + "/projects/" + encodeURIComponent(project.projectId) + "/senders");
    if (!Array.isArray(policy.senders) || !Number.isInteger(policy.revision)) throw new Error("Invalid scoped sender policy response.");
    dialog("Submission senders for " + project.name, "Scoped permission / independent of Viewer and Contributor", "Save scoped sender approval");
    sc.dialog = { senders: project };
    const body = $("supply-dialog-body");
    body.append(node("p", "These organisations may submit scoped immutable issues to this project without broad graph-read or Contributor access. This is not proposal trust, a document grant, or product acceptance. Existing project members remain eligible independently; removing a DID here does not revoke its project membership.", "supply-lineage"));
    const senders = field(body, "Approved sender organisation DIDs (one per line)", "textarea", policy.senders.join("\n"));
    $("supply-dialog-submit").hidden = false;
    $("supply-form").onsubmit = (event) => commitForm(event, async () => {
      const values = serials(senders.value);
      if (values.length > 100 || new Set(values).size !== values.length || values.some((did) => !did.startsWith("did:web:") || did.length > 512))
        throw new Error("Enter at most 100 distinct did:web organisation identities, one per line.");
      await api(base + "/projects/" + encodeURIComponent(project.projectId) + "/senders", "PUT", { expectedRevision: policy.revision, senders: values });
      $("supply-dialog").close(); notice("Scoped sender approval saved. Project roles, graph access and document grants are unchanged.");
    });
  }
  async function run(action) { try { await action(); } catch (error) { showError(error); } }
  window.clipSupplyChain = { isOwned, technicalStatus, eligibleSources, assertAllocation, compareData, sourcePin, pinKey, projectKey, destinationEligible, deliveryRequest, canManageAttachment, fields, renderCatalogue, renderSubmissions, renderSupply, selectProject, openDocument, openRetireDocument, openAdopt, openSenders, openRecord, openSubmission, openSubmissionDetail, openPublish, openFreeze, load, sc };
  initialize();
})();
