(function () {
  const form = document.querySelector(".live-preview-form");
  if (!form) return;

  const textarea = form.querySelector('textarea[name="original_message"], textarea[name="body"]');
  const senderSelect = form.querySelector('select[name="sender_id"]');
  const senderEmailInput = form.querySelector('input[name="sender_email"]');
  const receiverSelect = form.querySelector('select[name="receiver_id"]');
  const receiverEmailInput = form.querySelector('input[name="receiver_email"]');
  const receiverNameInput = form.querySelector('input[name="receiver_name"]');
  const organizationSelect = form.querySelector('select[name="organization_id"]');
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

  if (!textarea || !intentSelect || !panel || !statusNode || !listNode || !draftShell || !draftHighlightNode || !draftSuggestionLayer) return;

  const PREVIEW_DEBOUNCE_MS = Number(form.dataset.previewDebounceMs || 900);
  const MIN_DRAFT_LENGTH = Number(form.dataset.minPreviewLength || 1);
  const MIN_CHANGED_TEXT_LENGTH = Number(form.dataset.minChangedPreviewLength || 8);
  const SCORE_KEYS = ["clarity", "tone", "receiver_fit", "org_values_alignment"];
  const baseScores = {
    clarity: 80,
    tone: 90,
    receiver_fit: 70,
    org_values_alignment: 80,
  };

  let lastPreviewDraft = textarea.value || "";
  let debounceTimer = null;
  const activeRequests = new Map();
  let currentScores = { ...baseScores };
  let lastChangedTextHash = "";
  let nextReviewId = 1;
  let nextSuggestionId = 1;
  let reviewWindows = [];
  let priorReviewContext = [];
  const reviewedTextHashes = new Set();
  const settledSuggestionKeys = new Set();

  modeInputs.forEach((input) => {
    input.addEventListener("change", syncMode);
  });
  textarea.addEventListener("input", schedulePreview);
  textarea.addEventListener("scroll", syncDraftOverlay);
  window.addEventListener("resize", positionSuggestionChips);
  receiverSelect?.addEventListener("change", resetAndSchedulePreview);
  receiverEmailInput?.addEventListener("change", resetAndSchedulePreview);
  receiverNameInput?.addEventListener("change", resetAndSchedulePreview);
  organizationSelect?.addEventListener("change", resetAndSchedulePreview);
  channelSelect?.addEventListener("change", resetAndSchedulePreview);
  intentSelect.addEventListener("change", resetAndSchedulePreview);
  senderSelect?.addEventListener("change", resetAndSchedulePreview);
  senderEmailInput?.addEventListener("change", resetAndSchedulePreview);
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
    if (form.dataset.previewMode === "always") return "lightweight";
    return form.querySelector('input[name="suggestion_mode"]:checked')?.value || "full";
  }

  function schedulePreview() {
    if (currentMode() !== "lightweight") return;
    clearTimeout(debounceTimer);
    reconcileReviewWindows();
    renderDraftAnnotations();
    setStatus("Waiting for typing pause...");
    debounceTimer = setTimeout(runPreview, PREVIEW_DEBOUNCE_MS);
  }

  function resetAndSchedulePreview() {
    currentScores = { ...baseScores };
    reviewedTextHashes.clear();
    settledSuggestionKeys.clear();
    lastChangedTextHash = "";
    nextReviewId = 1;
    nextSuggestionId = 1;
    reviewWindows = [];
    priorReviewContext = [];
    abortActiveRequests();
    renderScores();
    renderDraftAnnotations();
    schedulePreview();
  }

  async function runPreview() {
    if (currentMode() !== "lightweight") return;

    const fullDraft = textarea.value || "";
    const changed = getChangedTextInfo(fullDraft);
    const changedText = changed.text;
    if (fullDraft.trim().length < MIN_DRAFT_LENGTH) {
      if (!fullDraft.trim()) clearDraftAnnotations();
      setStatus(`Type at least ${MIN_DRAFT_LENGTH} characters to preview suggestions.`);
      return;
    }
    if (!changedText.trim()) {
      renderDraftAnnotations();
      setStatus("Type a sentence or paragraph to preview suggestions.");
      return;
    }
    if (!isReviewableChangedText(changedText, fullDraft, changed)) {
      renderDraftAnnotations();
      setStatus("Waiting for a complete phrase before checking...");
      return;
    }

    const changedHash = hashText(changedText);
    if (reviewedTextHashes.has(changedHash)) {
      renderDraftAnnotations();
      setStatus("This text was already checked. Keep typing to ask again.");
      return;
    }

    const reviewWindow = createReviewWindow(changed, changedHash);
    const controller = new AbortController();
    activeRequests.set(reviewWindow.id, controller);
    lastChangedTextHash = changedHash;
    lastPreviewDraft = fullDraft;
    renderDraftAnnotations();
    setStatus("Checking changed text...");

    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfInput?.value || "",
        },
        signal: controller.signal,
        body: JSON.stringify(buildPreviewPayload(fullDraft, changedText, changed, reviewWindow)),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        setStatus(data.error || "Lightweight preview failed.");
        return;
      }

      const data = await response.json();
      if (!findReviewWindow(reviewWindow.id)) return;
      renderSuggestions(data.suggestions || [], data.text_hash || "", reviewWindow.id);
    } catch (error) {
      if (error.name !== "AbortError") {
        removeReviewWindow(reviewWindow.id);
        renderDraftAnnotations();
        setStatus("Lightweight preview failed.");
      }
    } finally {
      activeRequests.delete(reviewWindow.id);
    }
  }

  function getSenderId() {
    return form.dataset.senderId || senderSelect?.value || "";
  }

  function buildPreviewPayload(fullDraft, changedText, changedRangeInfo, reviewWindow) {
    const payload = {
      review_id: reviewWindow?.id ?? null,
      review_text: reviewWindow?.text || changedText,
      review_text_hash: reviewWindow?.textHash || "",
      channel: channelSelect?.value || form.dataset.channel || "gmail",
      intent: intentSelect.value,
      full_draft: fullDraft,
      changed_text: changedText,
      surrounding_context: getSurroundingContext(fullDraft, changedRangeInfo),
      prior_review_context: buildPriorReviewContext(),
    };

    if (form.dataset.identityMode === "email") {
      payload.organization_id = organizationSelect?.value || form.dataset.organizationId || "";
      payload.sender_email = senderEmailInput?.value || "";
      payload.receiver_email = receiverEmailInput?.value || "";
      payload.receiver_name = receiverNameInput?.value || "";
      return payload;
    }

    payload.sender_id = getSenderId();
    payload.receiver_id = receiverSelect?.value || "";
    return payload;
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
    while (start > 0 && /\S/.test(text[start - 1]) && !/[,.!?\n]/.test(text[start - 1])) {
      start -= 1;
    }
    while (end < text.length && /\S/.test(text[end]) && !/[,.!?\n]/.test(text[end])) {
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

  function isReviewableChangedText(changedText, fullDraft, changedRangeInfo) {
    const trimmed = String(changedText || "").trim();
    if (trimmed.length < MIN_CHANGED_TEXT_LENGTH) return false;
    if (endsInsideShortWord(fullDraft, changedRangeInfo)) return false;
    return true;
  }

  function endsInsideShortWord(fullDraft, changedRangeInfo) {
    const draftEnd = fullDraft.trimEnd().length;
    if ((changedRangeInfo?.end ?? 0) !== draftEnd) return false;

    const reviewedText = fullDraft.slice(changedRangeInfo.start, changedRangeInfo.end).trimEnd();
    return /[A-Za-z]{1,2}$/.test(reviewedText);
  }

  function getSurroundingContext(text, changedRangeInfo) {
    const anchorStart = changedRangeInfo?.start ?? textarea.selectionStart ?? text.length;
    const anchorEnd = changedRangeInfo?.end ?? textarea.selectionStart ?? text.length;
    const start = Math.max(0, anchorStart - 240);
    const end = Math.min(text.length, anchorEnd + 240);
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

  function createReviewWindow(changed, hash) {
    const reviewWindow = {
      id: nextReviewId++,
      start: changed.start,
      end: changed.end,
      text: changed.text,
      textHash: hash,
      status: "checking",
      suggestions: [],
    };
    reviewWindows.push(reviewWindow);
    return reviewWindow;
  }

  function buildPriorReviewContext() {
    const visibleContext = reviewWindows
      .filter((windowItem) => windowItem.status !== "checking")
      .map((windowItem) => ({
        id: windowItem.id,
        text: windowItem.text,
        text_hash: windowItem.textHash,
        status: windowItem.status,
        suggestions: windowItem.suggestions.map((suggestion) => ({
          target_text: suggestion.target_text || "",
          suggested_replacement: suggestion.suggested_replacement || "",
          issue: suggestion.issue || "",
          reason: suggestion.reason || "",
        })),
      }));
    return [...priorReviewContext, ...visibleContext].slice(-5);
  }

  function rememberReviewContext(windowItem, statusOverride) {
    if (!windowItem?.text) return;
    priorReviewContext.push({
      id: windowItem.id,
      text: windowItem.text,
      text_hash: windowItem.textHash,
      status: statusOverride || windowItem.status,
      suggestions: (windowItem.suggestions || []).map((suggestion) => ({
        target_text: suggestion.target_text || "",
        suggested_replacement: suggestion.suggested_replacement || "",
        issue: suggestion.issue || "",
        reason: suggestion.reason || "",
      })),
    });
    priorReviewContext = priorReviewContext.slice(-8);
  }

  function findReviewWindow(reviewId) {
    return reviewWindows.find((windowItem) => windowItem.id === reviewId);
  }

  function removeReviewWindow(reviewId) {
    reviewWindows = reviewWindows.filter((windowItem) => windowItem.id !== reviewId);
  }

  function abortActiveRequests() {
    activeRequests.forEach((controller) => controller.abort());
    activeRequests.clear();
  }

  function renderSuggestions(suggestions, hash, reviewId) {
    listNode.innerHTML = "";
    const reviewWindow = findReviewWindow(reviewId);
    if (!reviewWindow) return;
    const draft = textarea.value || "";
    const anchoredWindow = reanchorReviewWindow(reviewWindow, draft);
    if (!anchoredWindow) {
      removeReviewWindow(reviewId);
      renderDraftAnnotations();
      setStatus("Reviewed text changed before suggestions returned. Waiting for next pause...");
      return;
    }

    if (!suggestions.length) {
      reviewWindow.suggestions = [];
      rememberReviewContext(reviewWindow, "checked");
      removeReviewWindow(reviewWindow.id);
      if (hash || lastChangedTextHash) reviewedTextHashes.add(hash || lastChangedTextHash);
      renderDraftAnnotations();
      setStatus("No lightweight suggestions for this pause.");
      return;
    }

    const nextSuggestions = suggestions
      .map((suggestion) => ({
        ...suggestion,
        _index: nextSuggestionId++,
        _reviewId: reviewWindow.id,
        _range: resolveSuggestionRange(suggestion, reviewWindow),
      }))
      .filter((suggestion) => suggestion._range && !settledSuggestionKeys.has(suggestionKey(suggestion)));
    const visibleSuggestions = dedupeSuggestions(nextSuggestions);
    reviewWindow.status = visibleSuggestions.length ? "suggested" : "checked";
    reviewWindow.suggestions = visibleSuggestions;
    if (hash || lastChangedTextHash) reviewedTextHashes.add(hash || lastChangedTextHash);
    if (!visibleSuggestions.length) {
      rememberReviewContext(reviewWindow, "checked");
      removeReviewWindow(reviewWindow.id);
      renderDraftAnnotations();
      setStatus("No lightweight suggestions for this pause.");
      return;
    }
    sortReviewWindows();
    renderDraftAnnotations();
    setStatus(`${visibleSuggestions.length} lightweight suggestion${visibleSuggestions.length === 1 ? "" : "s"} - ${hash}`);
  }

  function dedupeSuggestions(suggestions) {
    const seen = new Set();
    return suggestions
      .slice()
      .filter((suggestion) => {
        const key = [
          suggestion._range?.start ?? "",
          suggestion._range?.end ?? "",
          suggestion.target_text || "",
          suggestion.suggested_replacement || "",
        ].join("\u0001");
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => a._range.start - b._range.start || suggestionPriority(a) - suggestionPriority(b) || rangeLength(a) - rangeLength(b) || a._index - b._index);
  }

  function suggestionPriority(suggestion) {
    return isCorrectionLike(suggestion) ? 0 : 1;
  }

  function rangeLength(suggestion) {
    return (suggestion._range?.end || 0) - (suggestion._range?.start || 0);
  }

  function isCorrectionLike(suggestion) {
    const target = String(suggestion.target_text || "");
    const replacement = String(suggestion.suggested_replacement || "");
    if (target.trim().split(/\s+/).length > 2 || replacement.trim().split(/\s+/).length > 2) return false;
    return Math.abs(target.length - replacement.length) <= 4;
  }

  function suggestionKey(suggestion) {
    return [
      suggestion.target_text || "",
      suggestion.suggested_replacement || "",
    ].join("\u0001");
  }

  function sortReviewWindows() {
    reviewWindows.sort((a, b) => a.start - b.start || a.id - b.id);
    reviewWindows.forEach((windowItem) => {
      windowItem.suggestions.sort((a, b) => {
        const aStart = a._range?.start ?? Number.MAX_SAFE_INTEGER;
        const bStart = b._range?.start ?? Number.MAX_SAFE_INTEGER;
        return aStart - bStart || a._index - b._index;
      });
    });
  }

  function dismissSuggestion(suggestionIndex) {
    const dismissed = allSuggestions().find((suggestion) => suggestion._index === suggestionIndex);
    if (dismissed) {
      settledSuggestionKeys.add(suggestionKey(dismissed));
    }
    reviewWindows.forEach((windowItem) => {
      windowItem.suggestions = windowItem.suggestions.filter((suggestion) => suggestion._index !== suggestionIndex);
      if (!windowItem.suggestions.length && windowItem.status === "suggested") {
        rememberReviewContext(windowItem, "dismissed");
        removeReviewWindow(windowItem.id);
      }
    });
    renderDraftAnnotations();
    if (!allSuggestions().length && lastChangedTextHash) {
      reviewedTextHashes.add(lastChangedTextHash);
      setStatus("Suggestions dismissed. This text will not be checked again unless it changes.");
    }
  }

  function resolveSuggestionRange(suggestion, reviewWindow) {
    const draft = textarea.value || "";
    const target = suggestion.target_text || "";
    if (!target) return null;

    if (suggestion._range && draft.slice(suggestion._range.start, suggestion._range.end) === target) {
      return suggestion._range;
    }

    if (reviewWindow) {
      const checkedText = draft.slice(reviewWindow.start, reviewWindow.end);
      const checkedOffset = checkedText.indexOf(target);
      if (checkedOffset >= 0) {
        return {
          start: reviewWindow.start + checkedOffset,
          end: reviewWindow.start + checkedOffset + target.length,
        };
      }
      return null;
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
    const reviewWindow = findReviewWindow(suggestion._reviewId);
    const range = suggestion._range || resolveSuggestionRange(suggestion, reviewWindow);
    const absolute = range ? range.start : -1;

    if (absolute < 0) {
      setStatus("Suggestion target no longer matches the draft.");
      return;
    }

    textarea.value = draft.slice(0, absolute) + replacement + draft.slice(range.end);
    settledSuggestionKeys.add(suggestionKey(suggestion));
    shiftReviewWindowsAfterEdit(range, replacement, replacement.length - (range.end - range.start), suggestion._index);
    lastPreviewDraft = textarea.value;
    applyScoreDeltas(suggestion.affected_scores || {});
    reviewWindows.forEach((windowItem) => {
      windowItem.suggestions = windowItem.suggestions.filter((item) => item._index !== suggestion._index);
      if (!windowItem.suggestions.length && windowItem.status === "suggested") {
        rememberReviewContext(windowItem, "accepted");
        removeReviewWindow(windowItem.id);
      }
    });
    renderDraftAnnotations();
    textarea.focus();
    setStatus("Suggestion accepted into draft.");
  }

  function shiftReviewWindowsAfterEdit(editedRange, replacement, delta, acceptedIndex) {
    const acceptedSuggestion = allSuggestions().find((item) => item._index === acceptedIndex);
    const updatedDraft = textarea.value || "";
    reviewWindows = reviewWindows.map((windowItem) => {
      const updatedWindow = rebaseReviewWindowAfterEdit(windowItem, editedRange, replacement, delta, updatedDraft);
      if (!updatedWindow) return null;

      const suggestions = windowItem.suggestions
        .map((suggestion) => rebaseSuggestionAfterEdit(suggestion, editedRange, replacement, delta, acceptedIndex, acceptedSuggestion, updatedDraft))
        .filter((suggestion) => suggestion);

      return {
        ...updatedWindow,
        status: updatedWindow.status === "checking" || suggestions.length ? updatedWindow.status : "checked",
        suggestions,
      };
    }).filter((windowItem) => windowItem);
  }

  function rebaseReviewWindowAfterEdit(windowItem, editedRange, replacement, delta, updatedDraft) {
    const mappedRange = mapRangeThroughEdit(windowItem, editedRange, replacement.length, true);
    if (!mappedRange) return null;
    const normalizedRange = normalizeRangeToText(updatedDraft, mappedRange);
    if (!normalizedRange) return null;

    return {
      ...windowItem,
      start: normalizedRange.start,
      end: normalizedRange.end,
      text: updatedDraft.slice(normalizedRange.start, normalizedRange.end).trim(),
    };
  }

  function rebaseSuggestionAfterEdit(suggestion, editedRange, replacement, delta, acceptedIndex, acceptedSuggestion, updatedDraft) {
    if (suggestion._index === acceptedIndex) return null;
    if (!suggestion._range) return suggestion;
    const mappedRange = mapRangeThroughEdit(suggestion._range, editedRange, replacement.length, false);
    if (!mappedRange) return null;
    const targetText = updatedDraft.slice(mappedRange.start, mappedRange.end);
    if (!targetText.trim()) return null;

    const acceptedTarget = String(acceptedSuggestion?.target_text || "");
    const currentReplacement = String(suggestion.suggested_replacement || "");
    return {
      ...suggestion,
      target_text: targetText,
      suggested_replacement: acceptedTarget && currentReplacement.includes(acceptedTarget)
        ? currentReplacement.replace(acceptedTarget, replacement)
        : suggestion.suggested_replacement,
      _range: mappedRange,
    };
  }

  function mapRangeThroughEdit(range, editedRange, replacementLength, keepFullyCovered) {
    const delta = replacementLength - (editedRange.end - editedRange.start);
    if (range.end <= editedRange.start) {
      return { start: range.start, end: range.end };
    }
    if (range.start >= editedRange.end) {
      return {
        start: range.start + delta,
        end: range.end + delta,
      };
    }

    const fullyCovered = range.start >= editedRange.start && range.end <= editedRange.end;
    if (fullyCovered && !keepFullyCovered) return null;

    const start = range.start < editedRange.start ? range.start : editedRange.start;
    const end = range.end > editedRange.end
      ? range.end + delta
      : editedRange.start + replacementLength;
    if (end <= start) return null;
    return { start, end };
  }

  function normalizeRangeToText(text, range) {
    let start = clampIndex(range.start, text.length);
    let end = clampIndex(range.end, text.length);
    while (start < end && /\s/.test(text[start])) {
      start += 1;
    }
    while (end > start && /\s/.test(text[end - 1])) {
      end -= 1;
    }
    if (end <= start) return null;
    return { start, end };
  }

  function reanchorReviewWindow(windowItem, draft) {
    const currentText = draft.slice(windowItem.start, windowItem.end).trim();
    if (currentText === windowItem.text) return windowItem;

    const anchorStart = findNearestTextAnchor(draft, windowItem.text, windowItem.start);
    if (anchorStart < 0) return null;

    windowItem.start = anchorStart;
    windowItem.end = anchorStart + windowItem.text.length;
    windowItem.suggestions.forEach((suggestion) => {
      suggestion._range = resolveSuggestionRange(suggestion, windowItem);
    });
    windowItem.suggestions = windowItem.suggestions.filter((suggestion) => suggestion._range);
    return windowItem;
  }

  function findNearestTextAnchor(draft, text, preferredStart) {
    if (!text) return -1;
    let bestIndex = -1;
    let bestDistance = Number.MAX_SAFE_INTEGER;
    let cursor = draft.indexOf(text);
    while (cursor >= 0) {
      const distance = Math.abs(cursor - preferredStart);
      if (distance < bestDistance) {
        bestIndex = cursor;
        bestDistance = distance;
      }
      cursor = draft.indexOf(text, cursor + 1);
    }
    return bestIndex;
  }

  function shiftSuggestionRange(suggestion, delta) {
    if (!suggestion._range) return suggestion;
    return {
      ...suggestion,
      _range: {
        start: suggestion._range.start + delta,
        end: suggestion._range.end + delta,
      },
    };
  }

  function reconcileReviewWindows() {
    const draft = textarea.value || "";
    reviewWindows = reviewWindows.filter((windowItem) => {
      if (windowItem.status === "checking") {
        reanchorReviewWindow(windowItem, draft);
        return true;
      }
      return Boolean(reanchorReviewWindow(windowItem, draft));
    });
    reviewWindows.forEach((windowItem) => {
      windowItem.suggestions.forEach((suggestion) => {
        suggestion._range = resolveSuggestionRange(suggestion, windowItem);
      });
      windowItem.suggestions = windowItem.suggestions.filter((suggestion) => suggestion._range);
      if (!windowItem.suggestions.length && windowItem.status === "suggested") {
        rememberReviewContext(windowItem, "checked");
        windowItem.status = "checked";
      }
    });
    reviewWindows = reviewWindows.filter((windowItem) => windowItem.status !== "checked");
  }

  function allSuggestions() {
    return reviewWindows.flatMap((windowItem) => windowItem.suggestions);
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

    reconcileReviewWindows();
    const ranges = [];
    reviewWindows.forEach((windowItem) => {
      if (windowItem.status === "checking" && windowItem.end > windowItem.start) {
        ranges.push({ start: windowItem.start, end: windowItem.end, type: "checked", reviewId: windowItem.id });
      }
      windowItem.suggestions.forEach((suggestion) => {
        suggestion._range = resolveSuggestionRange(suggestion, windowItem);
        if (suggestion._range && suggestion._range.end > suggestion._range.start) {
          ranges.push({
            ...suggestion._range,
            type: "suggested",
            index: suggestion._index,
            reviewId: windowItem.id,
            priority: suggestionPriority(suggestion),
            length: rangeLength(suggestion),
          });
        }
      });
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
        const suggestion = active
          .filter((range) => range.type === "suggested")
          .sort((a, b) => (a.priority || 0) - (b.priority || 0) || (a.length || 0) - (b.length || 0))[0];
        if (suggestion) classes.push("suggested");
        const data = suggestion ? ` data-suggestion-index="${suggestion.index}"` : "";
        return active.length
          ? `<span class="${classes.join(" ")}"${data}>${escapeHtml(text)}</span>`
          : escapeHtml(text);
      })
      .join("") || "&nbsp;";
  }

  function renderSuggestionChips() {
    const suggestions = allSuggestions();
    draftSuggestionLayer.innerHTML = suggestions
      .map((suggestion, orderIndex) => `
        <div class="draft-suggestion-chip" data-chip-index="${suggestion._index}">
          <strong>${orderIndex + 1}. ${escapeHtml(suggestion.issue || "Suggestion")}</strong>
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
        const suggestion = allSuggestions().find((item) => item._index === index);
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
    const placedRects = [];
    const gap = 8;
    sortReviewWindows();
    allSuggestions().forEach((suggestion) => {
      const chip = draftSuggestionLayer.querySelector(`[data-chip-index="${suggestion._index}"]`);
      const marker = draftHighlightNode.querySelector(`[data-suggestion-index="${suggestion._index}"]`);
      if (!chip || !marker) return;

      const markerRect = marker.getBoundingClientRect();
      const left = Math.max(8, Math.min(markerRect.left - shellRect.left, draftShell.clientWidth - chip.offsetWidth - 8));
      let top = markerRect.top - shellRect.top - chip.offsetHeight - 8;
      if (top < 8) {
        top = markerRect.bottom - shellRect.top + 6;
      }
      let candidate = rectForChip(left, top, chip);
      placedRects.forEach((placed) => {
        if (!rectsOverlap(candidate, placed)) return;
        top = placed.bottom + gap;
        candidate = rectForChip(left, top, chip);
      });
      chip.style.left = `${left}px`;
      chip.style.top = `${top}px`;
      placedRects.push(candidate);
    });
  }

  function rectForChip(left, top, chip) {
    return {
      left,
      top,
      right: left + chip.offsetWidth,
      bottom: top + chip.offsetHeight,
    };
  }

  function rectsOverlap(a, b) {
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  }

  function syncDraftOverlay() {
    draftHighlightNode.scrollTop = textarea.scrollTop;
    draftHighlightNode.scrollLeft = textarea.scrollLeft;
    positionSuggestionChips();
  }

  function clearDraftAnnotations() {
    abortActiveRequests();
    reviewWindows = [];
    priorReviewContext = [];
    draftHighlightNode.innerHTML = "";
    draftSuggestionLayer.innerHTML = "";
    listNode.innerHTML = "";
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

  function installTestHooks() {
    window.__livePreviewTestApi = {
      setDraft(value) {
        textarea.value = String(value || "");
        lastPreviewDraft = textarea.value;
      },
      resetState() {
        reviewWindows = [];
        priorReviewContext = [];
        reviewedTextHashes.clear();
        settledSuggestionKeys.clear();
        nextReviewId = 1;
        nextSuggestionId = 1;
      },
      seedReviewWindow({ start, end, text, status = "suggested", suggestions = [] }) {
        const reviewWindow = {
          id: nextReviewId++,
          start,
          end,
          text,
          textHash: hashText(text),
          status,
          suggestions: [],
        };
        reviewWindow.suggestions = suggestions
          .map((suggestion) => ({
            ...suggestion,
            _index: nextSuggestionId++,
            _reviewId: reviewWindow.id,
            _range: suggestion._range || resolveSuggestionRange(suggestion, reviewWindow),
          }))
          .filter((suggestion) => suggestion._range);
        reviewWindows.push(reviewWindow);
        sortReviewWindows();
        return {
          reviewId: reviewWindow.id,
          suggestionIds: reviewWindow.suggestions.map((suggestion) => suggestion._index),
        };
      },
      acceptSuggestion(index) {
        const suggestion = allSuggestions().find((item) => item._index === index);
        if (suggestion) acceptSuggestion(suggestion);
      },
      attachSuggestions(reviewId, suggestions, hash = "test-hash") {
        renderSuggestions(suggestions, hash, reviewId);
      },
      state() {
        return {
          draft: textarea.value,
          reviewWindows: JSON.parse(JSON.stringify(reviewWindows)),
          suggestions: JSON.parse(JSON.stringify(allSuggestions())),
          priorReviewContext: JSON.parse(JSON.stringify(priorReviewContext)),
        };
      },
    };
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

  if (window.__LIVE_PREVIEW_TEST_HOOKS__) {
    installTestHooks();
  }
})();
