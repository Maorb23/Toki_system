(function () {
  const root = document.getElementById("org-graph");
  if (!root || !window.d3) return;

  let employees = [];
  try {
    employees = JSON.parse(root.dataset.employees || "[]");
  } catch (err) {
    root.textContent = "Could not load graph data.";
    return;
  }

  const width = Math.max(root.clientWidth, 800);
  const height = Math.max(root.clientHeight, 640);

  const teams = Array.from(new Set(employees.map((e) => e.team || "No team")));
  const color = d3.scaleOrdinal(teams, d3.schemeSet2);

  root.innerHTML = "";

  const svg = d3
    .select(root)
    .append("svg")
    .attr("width", width)
    .attr("height", height)
    .attr("viewBox", `0 0 ${width} ${height}`);

  const legend = document.createElement("div");
  legend.className = "graph-legend";
  legend.innerHTML = teams
    .map(
      (team) => `
        <span class="legend-item">
          <span class="legend-dot" style="background:${color(team)}"></span>
          ${escapeHtml(team)}
        </span>
      `
    )
    .join("");
  root.appendChild(legend);

  const links = employees
    .filter((e) => e.manager_id)
    .map((e) => ({ source: e.manager_id, target: e.id }));

  const simulation = d3
    .forceSimulation(employees)
    .force(
      "link",
      d3.forceLink(links).id((d) => d.id).distance(140)
    )
    .force("charge", d3.forceManyBody().strength(-300))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(46));

  const link = svg
    .append("g")
    .attr("stroke", "#c8c1bb")
    .attr("stroke-opacity", 0.6)
    .selectAll("line")
    .data(links)
    .enter()
    .append("line")
    .attr("stroke-width", 1.5);

  const node = svg
    .append("g")
    .selectAll("g")
    .data(employees)
    .enter()
    .append("g")
    .attr("class", "graph-node")
    .on("click", (event, d) => {
      if (d.url) window.location.href = d.url;
    })
    .call(
      d3
        .drag()
        .on("start", dragstarted)
        .on("drag", dragged)
        .on("end", dragended)
    );

  node
    .append("circle")
    .attr("r", 22)
    .attr("fill", (d) => color(d.team || "No team"))
    .attr("stroke", "#1d1b18")
    .attr("stroke-width", 1.2);

  node
    .append("text")
    .attr("text-anchor", "middle")
    .attr("dy", 36)
    .attr("class", "graph-label")
    .text((d) => d.name);

  node
    .append("title")
    .text((d) => `${d.name} — ${d.role}`);

  simulation.on("tick", () => {
    link
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);

    node.attr("transform", (d) => `translate(${d.x},${d.y})`);
  });

  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
  }

  function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }

  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }
})();
