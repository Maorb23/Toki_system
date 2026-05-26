(function () {
  const BODY_SELECTOR = 'div[role="textbox"][contenteditable="true"]';
  const MIN_BODY_LENGTH = 20;
  const PREVIEW_DEBOUNCE_MS = 1000;
  const stateByEditor = new WeakMap();
  const handledSuggestionKeys = new WeakMap();
  let activeState = null;

  scanForComposeEditors();
  new MutationObserver(scanForComposeEditors).observe(document.documentElement, { childList: true, subtree: true });

  function scanForComposeEditors() {
    document.querySelectorAll(BODY_SELECTOR).forEach((editor) => {
      if (!isComposeBody(editor) || stateByEditor.has(editor)) return;
      attachPanel(editor);
    });
  }

  function isComposeBody(editor) {
    const label = `${editor.getAttribute("aria-label") || ""} ${editor.getAttribute("aria-multiline") || ""}`.toLowerCase();
    if (label.includes("message body")) return true;
    const compose = findComposeRoot(editor);
    return Boolean(compose && compose.querySelector('input[name="subjectbox"]'));
  }

  function attachPanel(editor) {
    const compose = findComposeRoot(editor);
    if (!compose) return;

    const panel = buildPanel();
    document.body.appendChild(panel);
    stopGmailEventBleed(panel);

    const launcher = buildLauncher();
    const launcherHost = findLauncherHost(compose, editor);
    launcherHost.insertAdjacentElement("afterend", launcher);

    const state = {
      editor,
      compose,
      panel,
      launcher,
      suggestions: [],
      debounceTimer: null,
      previewRequestId: 0,
      lastPreviewDraft: draftText(editor),
      checkedRange: null,
    };
    stateByEditor.set(editor, state);
    handledSuggestionKeys.set(editor, new Set());

    chrome.storage.sync.get({ organizationId: "1", senderEmail: "" }, (settings) => {
      field(panel, "organization_id").value = settings.organizationId || "1";
      field(panel, "sender_email").value = settings.senderEmail || "";
    });

    editor.addEventListener("input", () => schedulePreview(state));
    launcher.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      togglePanel(state);
    });
    panel.addEventListener("click", (event) => handlePanelClick(event, state));
    window.addEventListener("resize", () => {
      if (state.panel.classList.contains("is-open")) positionPanel(state);
    });
    document.addEventListener("scroll", () => {
      if (state.panel.classList.contains("is-open")) positionPanel(state);
    }, true);
  }

  function buildLauncher() {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "receiver-aware-launcher";
    button.textContent = "Toki";
    button.title = "Open Receiver-Aware settings";
    return button;
  }

  function buildPanel() {
    const panel = document.createElement("div");
    panel.className = "receiver-aware-panel";
    panel.innerHTML = `
      <div class="receiver-aware-header">
        <span>Toki Settings</span>
        <button type="button" data-close aria-label="Close">x</button>
      </div>
      <div class="receiver-aware-grid">
        <label>Org ID<input name="organization_id" placeholder="1"></label>
        <label>Intent
          <select name="intent">
            <option value="request">Request</option>
            <option value="update">Update</option>
            <option value="feedback">Feedback</option>
            <option value="decision">Decision</option>
            <option value="escalation">Escalation</option>
            <option value="alignment">Alignment</option>
          </select>
        </label>
        <label>Sender email<input name="sender_email" placeholder="sender@acme.test"></label>
        <label>Receiver email<input name="receiver_email" placeholder="receiver@acme.test"></label>
        <label>Receiver name<input name="receiver_name" placeholder="Dana Receiver"></label>
      </div>
      <div class="receiver-aware-actions">
        <button type="button" class="primary" data-preview>Preview inline suggestions</button>
        <button type="button" data-analyze>Analyze Draft</button>
      </div>
      <div class="receiver-aware-status">Pause typing for inline suggestions.</div>
      <div class="receiver-aware-results"></div>
    `;
    return panel;
  }

  function togglePanel(state) {
    if (activeState && activeState !== state) hidePanel(activeState);
    state.panel.classList.toggle("is-open");
    activeState = state.panel.classList.contains("is-open") ? state : null;
    if (state.panel.classList.contains("is-open")) {
      positionPanel(state);
      setTimeout(() => field(state.panel, "receiver_email").focus(), 0);
    }
  }

  function hidePanel(state) {
    state.panel.classList.remove("is-open");
    if (activeState === state) activeState = null;
  }

  function positionPanel(state) {
    const composeRect = state.compose.getBoundingClientRect();
    const width = Math.min(420, Math.max(340, window.innerWidth - 32));
    let left = composeRect.right + 12;
    if (left + width > window.innerWidth - 12) {
      left = Math.max(12, composeRect.left - width - 12);
    }
    let top = Math.max(12, composeRect.top);
    if (top + 520 > window.innerHeight) {
      top = Math.max(12, window.innerHeight - 520);
    }

    state.panel.style.width = `${width}px`;
    state.panel.style.left = `${left}px`;
    state.panel.style.top = `${top}px`;
  }

  function stopGmailEventBleed(panel) {
    ["pointerdown", "mousedown", "mouseup", "dblclick", "keydown", "keyup", "keypress", "input", "focusin", "focusout"].forEach((eventName) => {
      panel.addEventListener(eventName, (event) => event.stopPropagation(), true);
    });
  }

  function handlePanelClick(event, state) {
    event.stopPropagation();

    const closeButton = event.target.closest("[data-close]");
    if (closeButton) {
      event.preventDefault();
      hidePanel(state);
      return;
    }

    if (event.target.closest("[data-preview]")) {
      event.preventDefault();
      runPreview(state);
      return;
    }

    if (event.target.closest("[data-analyze]")) {
      event.preventDefault();
      runAnalyze(state);
      return;
    }

    const acceptButton = event.target.closest("[data-accept]");
    if (acceptButton) {
      event.preventDefault();
      acceptSuggestion(state, acceptButton.dataset.accept);
      return;
    }

    const dismissButton = event.target.closest("[data-dismiss]");
    if (dismissButton) {
      event.preventDefault();
      dismissSuggestion(state, dismissButton.dataset.dismiss);
    }
  }

  function schedulePreview(state) {
    clearTimeout(state.debounceTimer);
    setStatus(state, "Waiting for typing pause...");
    state.debounceTimer = setTimeout(() => runPreview(state), PREVIEW_DEBOUNCE_MS);
  }

  async function runPreview(state) {
    const draft = draftText(state.editor);
    if (draft.trim().length < MIN_BODY_LENGTH) {
      clearHighlights();
      renderResults(state, "");
      setStatus(state, `Type at least ${MIN_BODY_LENGTH} characters for inline suggestions.`);
      return;
    }

    const payload = payloadFromPanel(state);
    payload.full_draft = draft;
    const changed = getChangedTextInfo(state.lastPreviewDraft, draft);
    payload.changed_text = changed.text;
    payload.surrounding_context = surroundingContext(draft, changed);
    state.checkedRange = changed.start < changed.end ? { start: changed.start, end: changed.end } : null;

    const error = validatePayload(payload, true);
    if (error) {
      setStatus(state, error);
      return;
    }

    const requestId = ++state.previewRequestId;
    setStatus(state, "Checking inline suggestions...");
    const response = await apiRequest("/api/v1/integrations/gmail/inline-suggestions/preview/", payload);
    if (requestId !== state.previewRequestId) return;
    if (!response.ok) {
      setStatus(state, backendError(response));
      return;
    }

    state.lastPreviewDraft = draft;
    mergeSuggestions(state, response.data.suggestions || []);
    renderSuggestions(state);
    applyHighlights(state);
    setStatus(state, state.suggestions.length ? `${state.suggestions.length} suggestion(s) in play.` : "No inline suggestions.");
  }

  async function runAnalyze(state) {
    const payload = payloadFromPanel(state);
    payload.body = draftText(state.editor);
    const error = validatePayload(payload, false);
    if (error) {
      setStatus(state, error);
      return;
    }

    setStatus(state, "Running full analysis...");
    const response = await apiRequest("/api/v1/integrations/gmail/analyze-draft/", payload);
    if (!response.ok) {
      setStatus(state, backendError(response));
      return;
    }
    renderAnalysis(state, response.data);
    setStatus(state, "Analysis complete.");
  }

  function payloadFromPanel(state) {
    return {
      organization_id: field(state.panel, "organization_id").value.trim(),
      sender_email: field(state.panel, "sender_email").value.trim(),
      receiver_email: field(state.panel, "receiver_email").value.trim(),
      receiver_name: field(state.panel, "receiver_name").value.trim(),
      subject: subjectText(state.compose),
      intent: field(state.panel, "intent").value,
    };
  }

  function validatePayload(payload, preview) {
    if (!payload.organization_id) return "Organization ID is required.";
    if (!payload.sender_email) return "Sender email is required.";
    if (!payload.receiver_email) return "Receiver email is required.";
    if (preview && !payload.changed_text) return "Body text is required for preview.";
    if (!preview && !payload.body) return "Body text is required for analysis.";
    return "";
  }

  function mergeSuggestions(state, suggestions) {
    const handled = handledSuggestionKeys.get(state.editor);
    suggestions.forEach((suggestion) => {
      const key = suggestionKey(suggestion);
      if (!suggestion.target_text || !suggestion.suggested_replacement || handled.has(key)) return;
      if (state.suggestions.some((item) => suggestionKey(item) === key)) return;
      const range = resolveSuggestionRange(state, suggestion);
      if (!range) return;
      state.suggestions.push({
        ...suggestion,
        id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()),
        range,
      });
    });
    sortSuggestions(state);
  }

  function renderSuggestions(state) {
    const html = state.suggestions
      .map((suggestion, index) => `
        <div class="receiver-aware-card" data-suggestion-id="${escapeAttr(suggestion.id)}">
          <strong>${index + 1}. ${escapeHtml(suggestion.issue || "Suggestion")}</strong>
          <div>Target: ${escapeHtml(suggestion.target_text || "")}</div>
          <div class="receiver-aware-replacement">${escapeHtml(suggestion.suggested_replacement || "")}</div>
          <div>${escapeHtml(suggestion.reason || "")}</div>
          <div class="receiver-aware-actions">
            <button type="button" data-accept="${escapeAttr(suggestion.id)}">Accept</button>
            <button type="button" data-dismiss="${escapeAttr(suggestion.id)}">Dismiss</button>
          </div>
        </div>
      `)
      .join("");
    renderResults(state, html);
  }

  function renderAnalysis(state, data) {
    const scores = data.scores?.current || {};
    const scoreHtml = Object.keys(scores).map((key) => `${escapeHtml(key)}: ${escapeHtml(String(scores[key]))}`).join("<br>");
    const dashboard = data.dashboard_absolute_url || data.dashboard_url || "";
    renderResults(state, `
      <div class="receiver-aware-card">
        <strong>Scores</strong>
        <div>${scoreHtml || "No scores returned."}</div>
      </div>
      <div class="receiver-aware-card">
        <strong>Improved version</strong>
        <div class="receiver-aware-replacement">${escapeHtml(data.improved_version || "")}</div>
      </div>
      <div class="receiver-aware-card">
        <strong>Short version</strong>
        <div>${escapeHtml(data.short_version || "")}</div>
      </div>
      <div class="receiver-aware-card">
        <strong>Explanation</strong>
        <div>${escapeHtml(data.explanation || "")}</div>
        ${dashboard ? `<a href="${escapeAttr(dashboard)}" target="_blank" rel="noreferrer">Open dashboard</a>` : ""}
      </div>
    `);
  }

  function acceptSuggestion(state, id) {
    const suggestion = state.suggestions.find((item) => item.id === id);
    if (!suggestion) return;
    const range = findTextRange(state.editor, suggestion.target_text, suggestion.range);
    if (!range) {
      setStatus(state, "Suggestion target no longer matches the draft.");
      return;
    }
    range.deleteContents();
    range.insertNode(document.createTextNode(suggestion.suggested_replacement));
    handledSuggestionKeys.get(state.editor).add(suggestionKey(suggestion));
    shiftSuggestionRangesAfterEdit(state, suggestion, suggestion.suggested_replacement.length - suggestion.target_text.length);
    state.suggestions = state.suggestions.filter((item) => item.id !== id);
    state.editor.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: suggestion.suggested_replacement }));
    state.lastPreviewDraft = draftText(state.editor);
    renderSuggestions(state);
    applyHighlights(state);
    setStatus(state, "Suggestion accepted into Gmail draft.");
  }

  function dismissSuggestion(state, id) {
    const suggestion = state.suggestions.find((item) => item.id === id);
    if (suggestion) handledSuggestionKeys.get(state.editor).add(suggestionKey(suggestion));
    state.suggestions = state.suggestions.filter((item) => item.id !== id);
    renderSuggestions(state);
    applyHighlights(state);
    setStatus(state, "Suggestion dismissed.");
  }

  function applyHighlights(state) {
    clearHighlights();
    if (!("highlights" in CSS) || typeof Highlight === "undefined") return;
    const ranges = state.suggestions.map((suggestion) => findTextRange(state.editor, suggestion.target_text, suggestion.range)).filter(Boolean);
    if (ranges.length) CSS.highlights.set("receiver-aware-preview", new Highlight(...ranges));
  }

  function clearHighlights() {
    if ("highlights" in CSS) CSS.highlights.delete("receiver-aware-preview");
  }

  function findTextRange(root, target, preferredRange) {
    const needle = String(target || "");
    if (!needle) return null;
    const textIndex = textIndexFor(root);
    const combined = textIndex.combined;
    const nodes = textIndex.nodes;
    const preferred = rangeFromPreferredText(combined, needle, preferredRange);
    if (preferred) return charRangeToDomRange(nodes, preferred.start, preferred.end);

    const nearby = findExactNear(combined, needle, preferredRange?.start);
    if (nearby) return charRangeToDomRange(nodes, nearby.start, nearby.end);

    const normalized = findNormalizedTextRange(combined, needle, preferredRange?.start);
    if (normalized) return charRangeToDomRange(nodes, normalized.start, normalized.end);

    return null;
  }

  function textIndexFor(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let combined = "";
    let node;
    while ((node = walker.nextNode())) {
      nodes.push({ node, start: combined.length, end: combined.length + node.nodeValue.length });
      combined += node.nodeValue;
    }
    return { combined, nodes };
  }

  function rangeFromPreferredText(combined, needle, preferredRange) {
    if (!preferredRange) return null;
    const start = Math.max(0, Math.min(preferredRange.start, combined.length));
    const end = Math.max(start, Math.min(preferredRange.end, combined.length));
    const slice = combined.slice(start, end);
    if (slice === needle || normalizeForMatch(slice) === normalizeForMatch(needle)) {
      return { start, end };
    }
    return null;
  }

  function findExactNear(combined, needle, preferredStart) {
    if (typeof preferredStart === "number") {
      const windowStart = Math.max(0, preferredStart - 160);
      const windowEnd = Math.min(combined.length, preferredStart + needle.length + 160);
      const local = combined.slice(windowStart, windowEnd).indexOf(needle);
      if (local >= 0) return { start: windowStart + local, end: windowStart + local + needle.length };
    }

    const global = combined.indexOf(needle);
    return global >= 0 ? { start: global, end: global + needle.length } : null;
  }

  function findNormalizedTextRange(combined, needle, preferredStart) {
    const normalizedCombined = normalizeWithMap(combined);
    const normalizedNeedle = normalizeForMatch(needle);
    if (!normalizedNeedle) return null;

    const matches = [];
    let index = normalizedCombined.text.indexOf(normalizedNeedle);
    while (index >= 0) {
      const originalStart = normalizedCombined.map[index];
      const lastNormalizedIndex = index + normalizedNeedle.length - 1;
      const originalEnd = (normalizedCombined.map[lastNormalizedIndex] ?? originalStart) + 1;
      matches.push({ start: originalStart, end: originalEnd });
      index = normalizedCombined.text.indexOf(normalizedNeedle, index + 1);
    }
    if (!matches.length) return null;

    if (typeof preferredStart !== "number") return matches[0];
    return matches.sort((a, b) => Math.abs(a.start - preferredStart) - Math.abs(b.start - preferredStart))[0];
  }

  function normalizeWithMap(value) {
    let text = "";
    const map = [];
    let previousWasSpace = true;
    String(value || "").split("").forEach((char, index) => {
      if (/\s/.test(char)) {
        if (!previousWasSpace) {
          text += " ";
          map.push(index);
          previousWasSpace = true;
        }
        return;
      }
      text += char.toLowerCase();
      map.push(index);
      previousWasSpace = false;
    });
    if (text.endsWith(" ")) {
      text = text.slice(0, -1);
      map.pop();
    }
    return { text, map };
  }

  function normalizeForMatch(value) {
    return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function charRangeToDomRange(nodes, start, end) {
    const startNode = nodes.find((item) => start >= item.start && start <= item.end);
    const endNode = nodes.find((item) => end >= item.start && end <= item.end);
    if (!startNode || !endNode) return null;
    const range = document.createRange();
    range.setStart(startNode.node, start - startNode.start);
    range.setEnd(endNode.node, end - endNode.start);
    return range;
  }

  function resolveSuggestionRange(state, suggestion) {
    const draft = draftText(state.editor);
    const target = suggestion.target_text || "";
    if (!target) return null;

    if (state.checkedRange) {
      const checkedText = draft.slice(state.checkedRange.start, state.checkedRange.end);
      const offset = checkedText.indexOf(target);
      if (offset >= 0) {
        return {
          start: state.checkedRange.start + offset,
          end: state.checkedRange.start + offset + target.length,
        };
      }
    }

    const fallback = draft.indexOf(target);
    if (fallback < 0) return null;
    return { start: fallback, end: fallback + target.length };
  }

  function sortSuggestions(state) {
    state.suggestions.sort((a, b) => {
      const aStart = a.range?.start ?? Number.MAX_SAFE_INTEGER;
      const bStart = b.range?.start ?? Number.MAX_SAFE_INTEGER;
      return aStart - bStart;
    });
  }

  function shiftSuggestionRangesAfterEdit(state, acceptedSuggestion, delta) {
    const editedRange = acceptedSuggestion.range;
    if (!editedRange) return;
    state.suggestions = state.suggestions.map((suggestion) => {
      if (suggestion.id === acceptedSuggestion.id || !suggestion.range) return suggestion;
      if (suggestion.range.start >= editedRange.end) {
        return {
          ...suggestion,
          range: {
            start: suggestion.range.start + delta,
            end: suggestion.range.end + delta,
          },
        };
      }
      if (suggestion.range.end <= editedRange.start) return suggestion;
      return { ...suggestion, range: null };
    });
  }

  function apiRequest(path, payload) {
    return chrome.runtime.sendMessage({ type: "receiverAwareApi", path, payload });
  }

  function findLauncherHost(compose, editor) {
    return compose.querySelector('[aria-label="More options"]')?.closest("td, div") || editor.closest("table") || editor;
  }

  function findComposeRoot(editor) {
    return editor.closest('div[role="dialog"]') || editor.closest(".M9") || editor.closest(".nH");
  }

  function subjectText(compose) {
    return compose?.querySelector('input[name="subjectbox"]')?.value.trim() || "";
  }

  function draftText(editor) {
    return editor.innerText.replace(/\u00a0/g, " ").trim();
  }

  function getChangedTextInfo(previous, next) {
    if (!previous) {
      const start = Math.max(0, next.search(/\S/));
      const end = next.trimEnd().length;
      return { text: next.slice(start, end).trim(), start, end };
    }

    const diff = changedRange(previous, next);
    if (diff.start === diff.end) return { text: "", start: diff.start, end: diff.end };

    let start = diff.start;
    let end = diff.end;
    while (start > 0 && /\S/.test(next[start - 1]) && !/[.!?\n]/.test(next[start - 1])) {
      start -= 1;
    }
    while (end < next.length && /\S/.test(next[end]) && !/[.!?\n]/.test(next[end])) {
      end += 1;
    }

    const raw = next.slice(start, end);
    const leadingWhitespace = raw.search(/\S/);
    const normalizedStart = leadingWhitespace < 0 ? start : start + leadingWhitespace;
    const normalizedEnd = start + raw.trimEnd().length;
    return {
      text: next.slice(normalizedStart, normalizedEnd).trim(),
      start: normalizedStart,
      end: normalizedEnd,
    };
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

  function surroundingContext(draft, changed) {
    const midpoint = Math.floor((changed.start + changed.end) / 2);
    const start = Math.max(0, midpoint - 240);
    const end = Math.min(draft.length, midpoint + 240);
    return draft.slice(start, end);
  }

  function suggestionKey(suggestion) {
    return `${suggestion.target_text || ""}\u0001${suggestion.suggested_replacement || ""}`;
  }

  function field(panel, name) {
    return panel.querySelector(`[name="${name}"]`);
  }

  function setStatus(state, text) {
    state.panel.querySelector(".receiver-aware-status").textContent = text;
  }

  function renderResults(state, html) {
    state.panel.querySelector(".receiver-aware-results").innerHTML = html;
  }

  function backendError(response) {
    return response.data?.error || `Backend returned HTTP ${response.status}`;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#039;");
  }
})();
