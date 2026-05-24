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
  const draftShell = form.querySelector("[data-draft-shell]");
  const draftHighlightNode = form.querySelector("[data-draft-highlight]");
  const draftSuggestionLayer = form.querySelector("[data-draft-suggestion-layer]");
  const scoreInput = form.querySelector('input[name="lightweight_scores"]');
  const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');

  if (!textarea || !receiverSelect || !channelSelect || !intentSelect || !panel || !statusNode || !listNode || !draftShell || !draftHighlightNode || !draftSuggestionLayer) return;

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
  let checkedRange = null;
  let anchoredSuggestions = [];
  const reviewedTextHashes = new Set();

  modeInputs.forEach((input) => {
    input.addEventListener("change", syncMode);
  });
  textarea.addEventListener("input", schedulePreview);
  textarea.addEventListener("scroll", syncDraftOverlay);
  window.addEventListener("resize", positionSuggestionChips);
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
      renderDraftAnnotations();
      schedulePreview();
    } else {
      clearDraftAnnotations();
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
    checkedRange = null;
    anchoredSuggestions = [];
    renderScores();
    renderDraftAnnotations();
    schedulePreview();
  }

  async function runPreview() {
    if (currentMode() !== "lightweight") return;

    const fullDraft = textarea.value || "";
    const changed = getChangedTextInfo(fullDraft);
    const changedText = changed.text;
    if (!fullDraft.trim() || !changedText.trim()) {
      clearDraftAnnotations();
      setStatus("Type a sentence or paragraph to preview suggestions.");
      return;
    }

    const changedHash = hashText(changedText);
    if (reviewedTextHashes.has(changedHash)) {
      checkedRange = { start: changed.start, end: changed.end };
      anchoredSuggestions = [];
      renderDraftAnnotations();
      setStatus("This text was already checked. Keep typing to ask again.");
      return;
    }

    activeRequest?.abort();
    activeRequest = new AbortController();
    lastChangedTextHash = changedHash;
    checkedRange = { start: changed.start, end: changed.end };
    anchoredSuggestions = [];
    renderDraftAnnotations();
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

  function getChangedTextInfo(text) {
    if (!lastPreviewDraft) {
      const trimmedStart = text.search(/\S/);
      const start = trimmedStart < 0 ? 0 : trimmedStart;
      const end = text.trimEnd().length;
      return { text: text.slice(start, end).trim(), start, end };
    }
    const diff = changedRange(lastPreviewDraft, text);
    if (diff.start === diff.end) return { text: "", start: diff.start, end: diff.end };

    let start = diff.start;
    let end = diff.end;
    while (start > 0 && /\S/.test(text[start - 1]) && !/[.!?\n]/.test(text[start - 1])) {
      start -= 1;
    }
    while (end < text.length && /\S/.test(text[end]) && !/[.!?\n]/.test(text[end])) {
      end += 1;
    }
    const raw = text.slice(start, end);
    const leadingWhitespace = raw.search(/\S/);
    const normalizedStart = leadingWhitespace < 0 ? start : start + leadingWhitespace;
    const normalizedEnd = start + raw.trimEnd().length;
    return {
      text: text.slice(normalizedStart, normalizedEnd).trim(),
      start: normalizedStart,
      end: normalizedEnd,
    };
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
      anchoredSuggestions = [];
      renderDraftAnnotations();
      setStatus("No lightweight suggestions for this pause.");
      return;
    }

    anchoredSuggestions = suggestions
      .map((suggestion, index) => ({ ...suggestion, _index: index, _range: resolveSuggestionRange(suggestion) }))
      .filter((suggestion) => suggestion._range);
    renderDraftAnnotations();
    setStatus(`${suggestions.length} lightweight suggestion${suggestions.length === 1 ? "" : "s"} - ${hash}`);
  }

  function dismissSuggestion(suggestionIndex) {
    anchoredSuggestions = anchoredSuggestions.filter((suggestion) => suggestion._index !== suggestionIndex);
    renderDraftAnnotations();
    if (!anchoredSuggestions.length && lastChangedTextHash) {
      reviewedTextHashes.add(lastChangedTextHash);
      setStatus("Suggestions dismissed. This text will not be checked again unless it changes.");
    }
  }

  function resolveSuggestionRange(suggestion) {
    const draft = textarea.value || "";
    const target = suggestion.target_text || "";
    if (!target) return null;

    if (checkedRange) {
      const checkedText = draft.slice(checkedRange.start, checkedRange.end);
      const checkedOffset = checkedText.indexOf(target);
      if (checkedOffset >= 0) {
        return {
          start: checkedRange.start + checkedOffset,
          end: checkedRange.start + checkedOffset + target.length,
        };
      }
    }

    const fallback = draft.indexOf(target);
    if (fallback < 0) return null;
    return { start: fallback, end: fallback + target.length };
  }

  function acceptSuggestion(suggestion) {
    const target = suggestion.target_text || "";
    const replacement = suggestion.suggested_replacement || "";
    if (!target || !replacement) return;

    const draft = textarea.value;
    const range = suggestion._range || resolveSuggestionRange(suggestion);
    const absolute = range ? range.start : -1;

    if (absolute < 0) {
      setStatus("Suggestion target no longer matches the draft.");
      return;
    }

    textarea.value = draft.slice(0, absolute) + replacement + draft.slice(range.end);
    lastPreviewDraft = textarea.value;
    activeRequest?.abort();
    applyScoreDeltas(suggestion.affected_scores || {});
    checkedRange = null;
    anchoredSuggestions = anchoredSuggestions.filter((item) => item._index !== suggestion._index);
    renderDraftAnnotations();
    textarea.focus();
    setStatus("Suggestion accepted into draft.");
  }

  function applyScoreDeltas(deltas) {
    SCORE_KEYS.forEach((key) => {
      currentScores[key] = clampScore(Number(currentScores[key] || 0) + Number(deltas[key] || 0));
    });
    renderScores();
  }

  function renderDraftAnnotations() {
    const draft = textarea.value || "";
    if (!draft || currentMode() !== "lightweight") {
      clearDraftAnnotations();
      return;
    }

    const ranges = [];
    if (checkedRange && checkedRange.end > checkedRange.start) {
      ranges.push({ ...checkedRange, type: "checked" });
    }
    anchoredSuggestions.forEach((suggestion) => {
      if (suggestion._range && suggestion._range.end > suggestion._range.start) {
        ranges.push({ ...suggestion._range, type: "suggested", index: suggestion._index });
      }
    });

    renderHighlightLayer(draft, ranges);
    renderSuggestionChips();
    syncDraftOverlay();
  }

  function renderHighlightLayer(draft, ranges) {
    const boundaries = new Set([0, draft.length]);
    ranges.forEach((range) => {
      boundaries.add(clampIndex(range.start, draft.length));
      boundaries.add(clampIndex(range.end, draft.length));
    });

    const points = Array.from(boundaries).sort((a, b) => a - b);
    draftHighlightNode.innerHTML = points
      .slice(0, -1)
      .map((start, index) => {
        const end = points[index + 1];
        const text = draft.slice(start, end);
        const active = ranges.filter((range) => start >= range.start && end <= range.end);
        const classes = ["draft-mark"];
        if (active.some((range) => range.type === "checked")) classes.push("checked");
        const suggestion = active.find((range) => range.type === "suggested");
        if (suggestion) classes.push("suggested");
        const data = suggestion ? ` data-suggestion-index="${suggestion.index}"` : "";
        return active.length
          ? `<span class="${classes.join(" ")}"${data}>${escapeHtml(text)}</span>`
          : escapeHtml(text);
      })
      .join("") || "&nbsp;";
  }

  function renderSuggestionChips() {
    draftSuggestionLayer.innerHTML = anchoredSuggestions
      .map((suggestion) => `
        <div class="draft-suggestion-chip" data-chip-index="${suggestion._index}">
          <strong>${escapeHtml(suggestion.issue || "Suggestion")}</strong>
          <p>${escapeHtml(suggestion.reason || "")}</p>
          <div class="replacement">${escapeHtml(suggestion.suggested_replacement || "")}</div>
          <div class="suggestion-actions">
            <button class="button primary" type="button" data-accept-inline="${suggestion._index}">Accept</button>
            <button class="button" type="button" data-dismiss-inline="${suggestion._index}">Dismiss</button>
          </div>
        </div>
      `)
      .join("");

    draftSuggestionLayer.querySelectorAll("[data-accept-inline]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.acceptInline);
        const suggestion = anchoredSuggestions.find((item) => item._index === index);
        if (suggestion) acceptSuggestion(suggestion);
      });
    });
    draftSuggestionLayer.querySelectorAll("[data-dismiss-inline]").forEach((button) => {
      button.addEventListener("click", () => dismissSuggestion(Number(button.dataset.dismissInline)));
    });

    positionSuggestionChips();
  }

  function positionSuggestionChips() {
    if (currentMode() !== "lightweight") return;
    const shellRect = draftShell.getBoundingClientRect();
    anchoredSuggestions.forEach((suggestion) => {
      const chip = draftSuggestionLayer.querySelector(`[data-chip-index="${suggestion._index}"]`);
      const marker = draftHighlightNode.querySelector(`[data-suggestion-index="${suggestion._index}"]`);
      if (!chip || !marker) return;

      const markerRect = marker.getBoundingClientRect();
      const left = Math.max(8, Math.min(markerRect.left - shellRect.left, draftShell.clientWidth - chip.offsetWidth - 8));
      let top = markerRect.top - shellRect.top - chip.offsetHeight - 8;
      if (top < 8) {
        top = markerRect.bottom - shellRect.top + 6;
      }
      chip.style.left = `${left}px`;
      chip.style.top = `${top}px`;
    });
  }

  function syncDraftOverlay() {
    draftHighlightNode.scrollTop = textarea.scrollTop;
    draftHighlightNode.scrollLeft = textarea.scrollLeft;
    positionSuggestionChips();
  }

  function clearDraftAnnotations() {
    checkedRange = null;
    anchoredSuggestions = [];
    draftHighlightNode.innerHTML = "";
    draftSuggestionLayer.innerHTML = "";
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

  function clampScore(value) {
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, Math.round(value)));
  }

  function clampIndex(value, max) {
    return Math.max(0, Math.min(max, Number(value) || 0));
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
