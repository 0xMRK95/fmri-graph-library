const pageSize = 50;
let papers = [];
let filtered = [];
let page = 0;

const query = document.querySelector("#query");
const category = document.querySelector("#category");
const year = document.querySelector("#year");
const results = document.querySelector("#results");
const resultCount = document.querySelector("#result-count");
const pageStatus = document.querySelector("#page-status");
const previous = document.querySelector("#previous");
const next = document.querySelector("#next");

function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  const headers = rows.shift();
  return rows.filter((item) => item.length === headers.length).map((item) =>
    Object.fromEntries(headers.map((header, index) => [header, item[index]]))
  );
}

function paperURL(paper) {
  if (paper.doi) return `https://doi.org/${encodeURIComponent(paper.doi)}`;
  if (paper.arxiv) return `https://arxiv.org/abs/${encodeURIComponent(paper.arxiv)}`;
  if (paper.pubmed) return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(paper.pubmed)}/`;
  if (paper.pmc) return `https://pmc.ncbi.nlm.nih.gov/articles/${encodeURIComponent(paper.pmc)}/`;
  return null;
}

function categoryLabel(value) {
  return {
    fmri_gnn: "Graph & geometric deep learning",
    fmri_geometric_manifold: "Geometric / manifold / topological",
    fmri_graph_classical: "Classical graph analysis",
  }[value] || value;
}

function render() {
  const start = page * pageSize;
  const visible = filtered.slice(start, start + pageSize);
  results.replaceChildren(...visible.map((paper) => {
    const article = document.createElement("article");
    article.className = "paper";
    const title = document.createElement(paperURL(paper) ? "a" : "strong");
    title.className = "paper-title";
    title.textContent = paper.title || paper.paper_id;
    if (paperURL(paper)) {
      title.href = paperURL(paper);
      title.target = "_blank";
      title.rel = "noreferrer";
    }
    const meta = document.createElement("p");
    meta.textContent = [paper.year || "Year unknown", paper.venue || "Venue unknown", categoryLabel(paper.category)].join(" · ");
    article.append(title, meta);
    return article;
  }));
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  resultCount.textContent = `${filtered.length.toLocaleString()} papers`;
  pageStatus.textContent = `Page ${page + 1} of ${totalPages}`;
  previous.disabled = page === 0;
  next.disabled = page + 1 >= totalPages;
}

function applyFilters() {
  const needle = query.value.trim().toLowerCase();
  filtered = papers.filter((paper) => {
    const matchesQuery = !needle || Object.values(paper).some((value) => value.toLowerCase().includes(needle));
    return matchesQuery && (!category.value || paper.category === category.value) && (!year.value || paper.year === year.value);
  });
  page = 0;
  render();
}

fetch("data/nodes.csv")
  .then((response) => {
    if (!response.ok) throw new Error(`Catalog request failed: ${response.status}`);
    return response.text();
  })
  .then((text) => {
    papers = parseCSV(text);
    filtered = papers;
    [...new Set(papers.map((paper) => paper.year).filter(Boolean))]
      .sort((a, b) => Number(b) - Number(a))
      .forEach((value) => year.add(new Option(value, value)));
    render();
  })
  .catch(() => {
    resultCount.textContent = "The catalog could not be loaded.";
  });

[query, category, year].forEach((control) => control.addEventListener("input", applyFilters));
previous.addEventListener("click", () => { page -= 1; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
next.addEventListener("click", () => { page += 1; render(); window.scrollTo({ top: 0, behavior: "smooth" }); });
