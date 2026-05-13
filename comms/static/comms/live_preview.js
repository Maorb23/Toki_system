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
  const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');

  if (!textarea || !receiverSelect || !channelSelect || !intentSelect || !panel || !statusNode || !listNode) return;

  let lastPreviewDraft = textarea.value || "";
  let debounceTimer = null;
  let activeRequest = null;

  modeInputs.forEach((input) => {
    input.addEventListener("change", syncMode);
  });
  textarea.addEventListener("input", schedulePreview);
  receiverSelect.addEventListener("change", schedulePreview);
  channelSelect.addEventListener("change", schedulePreview);
  intentSelect.addEventListener("change", schedulePreview);
  senderSelect?.addEventListener("change", schedulePreview);

  form.addEventListener("submit", function (event) {
    if (currentMode() === "lightweight") {
      event.preventDefault();
      runPreview();
    }
  });

  syncMode();

  function syncMode() {
    const lightweight = currentMode() === "lightweight";
    panel.hidden = !lightweight;
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
    debounceTimer = setTimeout(runPreview, 2000);
  }

  async function runPreview() {
    if (currentMode() !== "lightweight") return;

    const fullDraft = textarea.value || "";
    const changedText = getChangedText(fullDraft);
    if (!fullDraft.trim() || !changedText.trim()) {
      listNode.innerHTML = "";
      setStatus("Type a sentence or paragraph to preview suggestions.");
      return;
    }

    activeRequest?.abort();
    activeRequest = new AbortController();
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
    const cursor = textarea.selectionStart ?? text.length;
    const paragraphStart = text.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1;
    const nextBreak = text.indexOf("\n", cursor);
    const paragraphEnd = nextBreak === -1 ? text.length : nextBreak;
    const paragraph = text.slice(paragraphStart, paragraphEnd).trim();
    if (paragraph) return paragraph;

    if (!lastPreviewDraft) return text.trim();
    const diff = changedRange(lastPreviewDraft, text);
    return text.slice(diff.start, diff.end).trim();
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
      item.querySelector("[data-dismiss]")?.addEventListener("click", () => item.remove());
      listNode.appendChild(item);
    });
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
    item.remove();
    textarea.focus();
    setStatus("Suggestion accepted into draft.");
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
})();
