(function () {
  const form = document.querySelector(".live-preview-form");
  if (!form) return;

  const textarea = form.querySelector('textarea[name="original_message"]');
  const senderSelect = form.querySelector('select[name="sender_id"]');
  const receiverSelect = form.querySelector('select[name="receiver_id"]');
  const channelSelect = form.querySelector('select[name="channel"]');
  const intentSelect = form.querySelector('select[name="intent"]');
  const modeInputs = Array.from(form.querySelectorAll('input[name="suggestion_mode"]'));
  const panel = form.querySelector("[data-live-preview-panel]");
  const statusNode = form.querySelector("[data-live-preview-status]");
  const listNode = form.querySelector("[data-live-preview-list]");
  const scoreListNode = form.querySelector("[data-live-score-list]");
  const checkedTextNode = form.querySelector("[data-live-checked-text]");
  const scoreInput = form.querySelector('input[name="lightweight_scores"]');
  const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');

  if (!textarea || !receiverSelect || !channelSelect || !intentSelect || !panel || !statusNode || !listNode) return;

  const PREVIEW_DEBOUNCE_MS = 700;
  const SCORE_KEYS = ["clarity", "tone", "receiver_fit", "org_values_alignment"];
  const baseScores = {
    clarity: 80,
    tone: 90,
    receiver_fit: 70,
    org_values_alignment: 80,
  };

  let lastPreviewDraft = textarea.value || "";
  let debounceTimer = null;
  let activeRequest = null;
  let currentScores = { ...baseScores };
  let lastChangedTextHash = "";
  const reviewedTextHashes = new Set();

  modeInputs.forEach((input) => {
    input.addEventListener("change", syncMode);
  });
  textarea.addEventListener("input", schedulePreview);
  receiverSelect.addEventListener("change", resetAndSchedulePreview);
  channelSelect.addEventListener("change", resetAndSchedulePreview);
  intentSelect.addEventListener("change", resetAndSchedulePreview);
  senderSelect?.addEventListener("change", resetAndSchedulePreview);
  renderScores();
  syncMode();

  function syncMode() {
    const lightweight = currentMode() === "lightweight";
    form.classList.toggle("is-lightweight", lightweight);
    if (lightweight) {
      setStatus("Pause typing for lightweight suggestions.");
      schedulePreview();
    }
  }

  function currentMode() {
    return form.querySelector('input[name="suggestion_mode"]:checked')?.value || "full";
  }

  function schedulePreview() {
    if (currentMode() !== "lightweight") return;
    clearTimeout(debounceTimer);
    setStatus("Waiting for typing pause...");
    debounceTimer = setTimeout(runPreview, PREVIEW_DEBOUNCE_MS);
  }

  function resetAndSchedulePreview() {
    currentScores = { ...baseScores };
    reviewedTextHashes.clear();
    lastChangedTextHash = "";
    renderScores();
    schedulePreview();
  }

  async function runPreview() {
    if (currentMode() !== "lightweight") return;

    const fullDraft = textarea.value || "";
    const changedText = getChangedText(fullDraft);
    if (!fullDraft.trim() || !changedText.trim()) {
      listNode.innerHTML = "";
      renderCheckedText("");
      setStatus("Type a sentence or paragraph to preview suggestions.");
      return;
    }

    const changedHash = hashText(changedText);
    if (reviewedTextHashes.has(changedHash)) {
      renderCheckedText(changedText);
      setStatus("This text was already checked. Keep typing to ask again.");
      return;
    }

    activeRequest?.abort();
    activeRequest = new AbortController();
    lastChangedTextHash = changedHash;
    renderCheckedText(changedText);
    setStatus("Checking changed text...");

    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfInput?.value || "",
        },
        signal: activeRequest.signal,
        body: JSON.stringify({
          sender_id: getSenderId(),
          receiver_id: receiverSelect.value,
          channel: channelSelect.value,
          intent: intentSelect.value,
          full_draft: fullDraft,
          changed_text: changedText,
          surrounding_context: getSurroundingContext(fullDraft),
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setStatus(data.error || "Lightweight preview failed.");
        return;
      }

      const data = await response.json();
      lastPreviewDraft = fullDraft;
      renderSuggestions(data.suggestions || [], data.text_hash || "");
    } catch (error) {
      if (error.name !== "AbortError") {
        setStatus("Lightweight preview failed.");
      }
    }
  }

  function getSenderId() {
    return form.dataset.senderId || senderSelect?.value || "";
  }

  function getChangedText(text) {
    if (!lastPreviewDraft) return text.trim();
    const diff = changedRange(lastPreviewDraft, text);
    if (diff.start === diff.end) return "";

    let start = diff.start;
    let end = diff.end;
    while (start > 0 && /\S/.test(text[start - 1]) && !/[.!?\n]/.test(text[start - 1])) {
      start -= 1;
    }
    while (end < text.length && /\S/.test(text[end]) && !/[.!?\n]/.test(text[end])) {
      end += 1;
    }
    return text.slice(start, end).trim();
  }

  function getSurroundingContext(text) {
    const cursor = textarea.selectionStart ?? text.length;
    const start = Math.max(0, cursor - 240);
    const end = Math.min(text.length, cursor + 240);
    return text.slice(start, end);
  }

  function changedRange(previous, next) {
    let start = 0;
    while (start < previous.length && start < next.length && previous[start] === next[start]) {
      start += 1;
    }

    let previousEnd = previous.length;
    let nextEnd = next.length;
    while (previousEnd > start && nextEnd > start && previous[previousEnd - 1] === next[nextEnd - 1]) {
      previousEnd -= 1;
      nextEnd -= 1;
    }

    return { start, end: nextEnd };
  }

  function renderSuggestions(suggestions, hash) {
    listNode.innerHTML = "";
    if (!suggestions.length) {
      if (lastChangedTextHash) reviewedTextHashes.add(lastChangedTextHash);
      setStatus("No lightweight suggestions for this pause.");
      return;
    }

    setStatus(`${suggestions.length} lightweight suggestion${suggestions.length === 1 ? "" : "s"} - ${hash}`);
    suggestions.forEach((suggestion, index) => {
      const item = document.createElement("div");
      item.className = "live-preview-item";
      item.innerHTML = `
        <strong>${escapeHtml(suggestion.issue || "Suggestion")}</strong>
        <p>${escapeHtml(suggestion.reason || "")}</p>
        <div class="replacement">${escapeHtml(suggestion.suggested_replacement || "")}</div>
        ${renderScoreDeltas(suggestion.affected_scores || {})}
        <div class="suggestion-actions">
          <button class="button primary" type="button" data-index="${index}">Accept</button>
          <button class="button" type="button" data-dismiss="${index}">Dismiss</button>
        </div>
      `;

      item.querySelector("[data-index]")?.addEventListener("click", () => acceptSuggestion(suggestion, item));
      item.querySelector("[data-dismiss]")?.addEventListener("click", () => dismissSuggestion(item));
      listNode.appendChild(item);
    });
  }

  function dismissSuggestion(item) {
    item.remove();
    if (!listNode.children.length && lastChangedTextHash) {
      reviewedTextHashes.add(lastChangedTextHash);
      setStatus("Suggestions dismissed. This text will not be checked again unless it changes.");
    }
  }

  function renderScoreDeltas(scores) {
    const entries = Object.entries(scores);
    if (!entries.length) return "";
    return `<ul class="score-deltas">${entries
      .map(([key, value]) => `<li><strong>${escapeHtml(titleCase(key))}:</strong> ${Number(value)}</li>`)
      .join("")}</ul>`;
  }

  function acceptSuggestion(suggestion, item) {
    const target = suggestion.target_text || "";
    const replacement = suggestion.suggested_replacement || "";
    if (!target || !replacement) return;

    const draft = textarea.value;
    const cursor = textarea.selectionStart ?? draft.length;
    const paragraphStart = draft.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
    const nextBreak = draft.indexOf("\n", cursor);
    const paragraphEnd = nextBreak === -1 ? draft.length : nextBreak;
    const paragraph = draft.slice(paragraphStart, paragraphEnd);
    let found = paragraph.indexOf(target);
    let absolute = -1;

    if (found >= 0) {
      absolute = paragraphStart + found;
    } else {
      found = draft.indexOf(target);
      absolute = found;
    }

    if (absolute < 0) {
      setStatus("Suggestion target no longer matches the draft.");
      return;
    }

    textarea.value = draft.slice(0, absolute) + replacement + draft.slice(absolute + target.length);
    lastPreviewDraft = textarea.value;
    activeRequest?.abort();
    applyScoreDeltas(suggestion.affected_scores || {});
    item.remove();
    textarea.focus();
    setStatus("Suggestion accepted into draft.");
  }

  function applyScoreDeltas(deltas) {
    SCORE_KEYS.forEach((key) => {
      currentScores[key] = clampScore(Number(currentScores[key] || 0) + Number(deltas[key] || 0));
    });
    renderScores();
  }

  function renderScores() {
    if (scoreInput) {
      scoreInput.value = JSON.stringify(currentScores);
    }
    if (!scoreListNode) return;

    scoreListNode.innerHTML = SCORE_KEYS.map((key) => {
      const value = clampScore(currentScores[key]);
      return `
        <div class="live-score-row">
          <span>${escapeHtml(titleCase(key))}</span>
          <strong>${value}</strong>
        </div>
      `;
    }).join("");
  }

  function renderCheckedText(value) {
    if (!checkedTextNode) return;
    checkedTextNode.classList.toggle("has-content", Boolean(value));
    checkedTextNode.innerHTML = value
      ? `<span>Checked text</span><p>${escapeHtml(value)}</p>`
      : "";
  }

  function clampScore(value) {
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, Math.round(value)));
  }

  function setStatus(value) {
    statusNode.textContent = value;
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

  function hashText(value) {
    let hash = 0;
    const normalized = String(value).trim();
    for (let index = 0; index < normalized.length; index += 1) {
      hash = ((hash << 5) - hash + normalized.charCodeAt(index)) | 0;
    }
    return String(hash);
  }
})();
