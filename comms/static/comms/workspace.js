(function () {
  const editor = document.getElementById("inline-editor");
  if (!editor) return;

  const messageText = document.getElementById("message-text");
  const finalText = document.getElementById("final-text");
  const scorePanel = document.getElementById("score-panel");

  const csrf = editor.dataset.csrf;
  const decisionUrlTemplate = editor.dataset.decisionUrlTemplate;
  const bulkUrl = editor.dataset.bulkUrl;

  const originalText = editor.dataset.originalText || messageText.textContent || "";
  let currentDraftText = messageText.textContent || originalText;

  let suggestions = [];
  try {
    suggestions = JSON.parse(editor.dataset.suggestions || "[]");
  } catch (err) {
    console.error("Invalid suggestion JSON", err);
    suggestions = [];
  }

  renderInlineText();

  document.getElementById("accept-all")?.addEventListener("click", () => bulkDecision("accepted"));
  document.getElementById("reject-all")?.addEventListener("click", () => bulkDecision("rejected"));

  // Event delegation: attach once.
  messageText.addEventListener("click", function (event) {
    const target = event.target.closest(".inline-segment");
    if (!target) return;
    showPopover(target);
  });

  function renderInlineText() {
    const pending = suggestions
      .filter((s) => s.decision === "pending")
      .map(resolveSuggestionSpan)
      .filter(Boolean)
      .sort((a, b) => a.start_index - b.start_index || b.end_index - a.end_index);

    let html = "";
    let cursor = 0;

    for (const s of pending) {
      const start = s.start_index;
      const end = s.end_index;

      if (start < cursor) continue;

      html += escapeHtml(currentDraftText.slice(cursor, start));
      html += `<span class="inline-segment" data-suggestion-id="${escapeHtml(String(s.id))}">${escapeHtml(currentDraftText.slice(start, end))}</span>`;
      cursor = end;
    }

    html += escapeHtml(currentDraftText.slice(cursor));
    messageText.innerHTML = html;
  }

  function resolveSuggestionSpan(suggestion) {
    const target = suggestion.target_text || "";

    let start = Number.isInteger(suggestion.start_index) ? suggestion.start_index : -1;
    let end = Number.isInteger(suggestion.end_index) ? suggestion.end_index : -1;

    const indexSpanValid =
      start >= 0 &&
      end > start &&
      end <= currentDraftText.length &&
      currentDraftText.slice(start, end) === target;

    if (indexSpanValid) {
      return { ...suggestion, start_index: start, end_index: end };
    }

    const found = currentDraftText.indexOf(target);
    if (found < 0) return null;

    return {
      ...suggestion,
      start_index: found,
      end_index: found + target.length,
    };
  }

  function showPopover(targetNode) {
    closePopovers();

    const id = targetNode.dataset.suggestionId;
    const suggestion = suggestions.find((s) => String(s.id) === String(id));
    if (!suggestion) return;

    const pop = document.createElement("div");
    pop.className = "suggestion-popover";

    const scores = suggestion.affected_scores || {};
    const scoreItems = Object.entries(scores)
      .map(([key, value]) => `<li><strong>${escapeHtml(titleCase(key))}:</strong> ${Number(value)}</li>`)
      .join("");

    pop.innerHTML = `
      <strong>${escapeHtml(suggestion.issue || "Suggestion")}</strong>
      <p>${escapeHtml(suggestion.reason || "")}</p>
      <div class="replacement">${escapeHtml(suggestion.suggested_replacement || "")}</div>
      ${scoreItems ? `<ul class="score-deltas">${scoreItems}</ul>` : ""}
      <div class="suggestion-actions">
        <button class="button primary" data-action="accepted">Accept</button>
        <button class="button" data-action="rejected">Reject</button>
      </div>
    `;

    document.body.appendChild(pop);
    positionPopover(pop, targetNode);

    pop.addEventListener("click", function (event) {
      event.stopPropagation();
    });

    pop.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", async function () {
        await setDecision(id, button.dataset.action);
        closePopovers();
      });
    });

    setTimeout(() => {
      window.addEventListener("click", closePopovers, { once: true });
    }, 0);
  }

  function closePopovers() {
    document.querySelectorAll(".suggestion-popover").forEach((node) => node.remove());
  }

  function positionPopover(pop, targetNode) {
    pop.style.position = "fixed";
    pop.style.visibility = "hidden";

    requestAnimationFrame(() => {
      const rect = targetNode.getBoundingClientRect();
      const popRect = pop.getBoundingClientRect();
      const padding = 10;

      let top = rect.top - popRect.height - padding;
      if (top < padding) top = rect.bottom + padding;

      let left = rect.left;
      if (left + popRect.width > window.innerWidth - padding) {
        left = window.innerWidth - popRect.width - padding;
      }
      if (left < padding) left = padding;

      pop.style.top = `${top}px`;
      pop.style.left = `${left}px`;
      pop.style.visibility = "visible";
    });
  }

  async function setDecision(suggestionId, decision) {
    const url = decisionUrlTemplate.replace("999999", String(suggestionId));

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify({ decision }),
    });

    if (!response.ok) {
      alert(await response.text());
      return;
    }

    const data = await response.json();

    suggestions = suggestions.map((s) =>
      String(s.id) === String(suggestionId) ? { ...s, decision } : s
    );

    updateScorePanel(data.current_scores);
    setDraftText(data.final_text);

    if (finalText) {
      finalText.textContent = data.final_text || "";
    }

    renderInlineText();
  }

  async function bulkDecision(decision) {
    const response = await fetch(bulkUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify({ decision }),
    });

    if (!response.ok) {
      alert(await response.text());
      return;
    }

    const data = await response.json();

    suggestions = suggestions.map((s) => ({ ...s, decision }));

    updateScorePanel(data.current_scores);
    setDraftText(data.final_text);

    if (finalText) {
      finalText.textContent = data.final_text || "";
    }

    renderInlineText();
  }

  function setDraftText(value) {
    currentDraftText = typeof value === "string" ? value : originalText;
  }

  function updateScorePanel(scores) {
    if (!scorePanel) return;

    scorePanel.innerHTML = "";

    for (const [key, value] of Object.entries(scores || {})) {
      const row = document.createElement("div");
      row.innerHTML = `
        <span>${escapeHtml(titleCase(key))}</span>
        <progress max="100" value="${Number(value)}"></progress>
        <strong>${Number(value)}</strong>
      `;
      scorePanel.appendChild(row);
    }
  }

  function titleCase(value) {
    return String(value)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());
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
