import Graph from "https://cdn.jsdelivr.net/npm/graphology@0.26.0/+esm";
import Sigma from "https://cdn.jsdelivr.net/npm/sigma@3.0.2/+esm";

const container = document.querySelector("#sigma-container");
const loading = document.querySelector("#map-loading");
const search = document.querySelector("#map-search");
const searchResults = document.querySelector("#search-results");
const card = document.querySelector("#paper-card");
const enabledCategories = new Set([
  "fmri_gnn",
  "fmri_geometric_manifold",
  "fmri_graph_classical",
]);

const categoryNames = {
  fmri_gnn: "Graph & geometric deep learning",
  fmri_geometric_manifold: "Geometric / manifold / topological",
  fmri_graph_classical: "Classical graph analysis",
};

let graph;
let renderer;
let activeNode = null;
let hoveredNode = null;
let showAllEdges = false;
let currentLayout = "timeline";

function sourceURL(attributes) {
  if (attributes.doi) return `https://doi.org/${encodeURIComponent(attributes.doi)}`;
  if (attributes.arxiv) return `https://arxiv.org/abs/${encodeURIComponent(attributes.arxiv)}`;
  if (attributes.pubmed) return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(attributes.pubmed)}/`;
  if (attributes.pmc) return `https://pmc.ncbi.nlm.nih.gov/articles/${encodeURIComponent(attributes.pmc)}/`;
  return null;
}

function showPaper(node) {
  activeNode = node;
  const attributes = graph.getNodeAttributes(node);
  document.querySelector("#paper-category").textContent = categoryNames[attributes.category] || attributes.category;
  document.querySelector("#paper-title").textContent = attributes.title || attributes.label || node;
  document.querySelector("#paper-meta").textContent = [attributes.year || "Year unknown", attributes.venue || "Venue unknown"].join(" · ");
  document.querySelector("#paper-degree").innerHTML = `<span>${attributes.in_degree} incoming</span><span>${attributes.out_degree} outgoing</span><span>${attributes.degree} total</span>`;
  const link = document.querySelector("#paper-link");
  const url = sourceURL(attributes);
  link.hidden = !url;
  if (url) link.href = url;
  card.style.setProperty("--card-color", attributes.color);
  card.hidden = false;
  renderer.refresh();
}

function focusNode(node) {
  const display = renderer.getNodeDisplayData(node);
  if (!display) return;
  renderer.getCamera().animate(
    { x: display.x, y: display.y, ratio: 0.12 },
    { duration: 650 },
  );
  showPaper(node);
  searchResults.classList.remove("active");
}

function updateSearch() {
  const needle = search.value.trim().toLowerCase();
  searchResults.replaceChildren();
  if (needle.length < 2) {
    searchResults.classList.remove("active");
    return;
  }
  const matches = graph.nodes().filter((node) => {
    const attributes = graph.getNodeAttributes(node);
    return [attributes.title, attributes.venue, attributes.year, attributes.doi]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle));
  }).slice(0, 8);
  matches.forEach((node) => {
    const attributes = graph.getNodeAttributes(node);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "search-result";
    button.textContent = `${attributes.title || node} · ${attributes.year || "n.d."}`;
    button.addEventListener("click", () => focusNode(node));
    searchResults.append(button);
  });
  searchResults.classList.toggle("active", matches.length > 0);
}

function relatedToActive(node) {
  const focus = hoveredNode || activeNode;
  return !focus || node === focus || graph.areNeighbors(node, focus);
}

function switchLayout(layout) {
  currentLayout = layout;
  graph.updateEachNodeAttributes((node, attributes) => ({
    ...attributes,
    x: attributes[`${layout}_x`],
    y: attributes[`${layout}_y`],
  }));
  document.querySelectorAll("[data-layout]").forEach((button) => {
    button.classList.toggle("active", button.dataset.layout === layout);
  });
  document.querySelector("#timeline-cue").classList.toggle("hidden", layout !== "timeline");
  renderer.refresh();
  renderer.getCamera().animatedReset({ duration: 650 });
}

fetch("data/network.json")
  .then((response) => {
    if (!response.ok) throw new Error(`Network request failed: ${response.status}`);
    return response.json();
  })
  .then((data) => {
    graph = new Graph({ type: "directed", multi: false, allowSelfLoops: false });
    graph.import(data);
    renderer = new Sigma(graph, container, {
      allowInvalidContainer: false,
      defaultEdgeColor: "rgba(80, 99, 95, 0.10)",
      defaultEdgeType: "line",
      labelColor: { color: "#18332f" },
      labelDensity: 0.02,
      labelGridCellSize: 180,
      labelRenderedSizeThreshold: 8.5,
      renderEdgeLabels: false,
      zIndex: true,
      nodeReducer: (node, attributes) => {
        if (!enabledCategories.has(attributes.category)) return { ...attributes, hidden: true };
        const focus = hoveredNode || activeNode;
        if (!focus) return attributes;
        if (node === focus) return { ...attributes, size: attributes.size * 1.65, zIndex: 3, highlighted: true };
        if (relatedToActive(node)) return { ...attributes, size: attributes.size * 1.18, zIndex: 2 };
        return { ...attributes, color: "#cbd2cf", size: Math.max(1, attributes.size * 0.55), zIndex: 0 };
      },
      edgeReducer: (edge, attributes) => {
        const [source, target] = graph.extremities(edge);
        if (!enabledCategories.has(graph.getNodeAttribute(source, "category"))
          || !enabledCategories.has(graph.getNodeAttribute(target, "category"))) {
          return { ...attributes, hidden: true };
        }
        const focus = hoveredNode || activeNode;
        if (focus && (source === focus || target === focus)) {
          return { ...attributes, color: "rgba(21, 85, 78, 0.58)", size: 1.05, zIndex: 2 };
        }
        if (showAllEdges) return { ...attributes, color: "rgba(80, 99, 95, 0.055)", size: 0.12 };
        return { ...attributes, hidden: true };
      },
    });
    renderer.on("clickNode", ({ node }) => showPaper(node));
    renderer.on("clickStage", () => {
      activeNode = null;
      card.hidden = true;
      renderer.refresh();
    });
    renderer.on("enterNode", ({ node }) => {
      hoveredNode = node;
      renderer.refresh();
    });
    renderer.on("leaveNode", () => {
      hoveredNode = null;
      renderer.refresh();
    });
    loading.classList.add("done");
  })
  .catch((error) => {
    loading.querySelector("p").textContent = `Map could not load: ${error.message}`;
  });

search.addEventListener("input", updateSearch);
document.querySelectorAll('.legend-row input[type="checkbox"]').forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) enabledCategories.add(checkbox.value);
    else enabledCategories.delete(checkbox.value);
    renderer?.refresh();
  });
});
document.querySelectorAll("[data-layout]").forEach((button) => {
  button.addEventListener("click", () => switchLayout(button.dataset.layout));
});
document.querySelector("#show-edges").addEventListener("change", (event) => {
  showAllEdges = event.target.checked;
  renderer?.refresh();
});
document.querySelector("#reset-map").addEventListener("click", () => renderer?.getCamera().animatedReset({ duration: 600 }));
document.querySelector("#close-card").addEventListener("click", () => {
  activeNode = null;
  card.hidden = true;
  renderer?.refresh();
});
