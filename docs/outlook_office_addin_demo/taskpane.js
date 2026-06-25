(function () {
  const STORAGE_KEY = "tokiOutlookDemoSettings";
  const LIVE_PREVIEW_POLL_MS = 750;
  const LIVE_PREVIEW_DEBOUNCE_MS = 1200;
  const MIN_PREVIEW_BODY_LENGTH = 20;
  const ENDPOINTS = {
    analyze: "/api/v1/integrations/outlook/analyze-draft/",
    preview: "/api/v1/integrations/outlook/inline-suggestions/preview/",
    event: "/api/v1/integrations/outlook/events/",
  };

  const state = {
    officeReady: false,
    lastAnalysis: null,
    lastImprovedVersion: "",
    previewSuggestions: [],
    dismissedSuggestionKeys: [],
    dismissedSuggestionContext: [],
    livePreviewEnabled: false,
    livePreviewPollTimer: null,
    livePreviewDebounceTimer: null,
    livePreviewRequestInFlight: false,
    pendingPreviewRefreshReason: null,
    lastObservedBody: "",
    lastPreviewedBody: "",
    previewRequestId: 0,
    bodyMutationVersion: 0,
    debugEnabled: false,
    debugEvents: [],
  };

  const fields = {};

  document.addEventListener("DOMContentLoaded", () => {
    bindElements();
    restoreSettings();
    bindEvents();

    if (window.Office) {
      Office.onReady(() => {
        state.officeReady = Boolean(Office.context && Office.context.mailbox && Office.context.mailbox.item);
        if (state.officeReady) {
          setStatus("Ready. Refresh draft details or analyze the current compose draft.");
          hydrateFromDraft(false);
          if (state.livePreviewEnabled) startLivePreview();
        } else {
          setStatus("Open this task pane from an Outlook compose draft.", true);
        }
      });
    } else {
      setStatus("Office.js is not loaded. Open this page from Outlook compose.", true);
    }
  });

  function bindElements() {
    [
      "backendUrl",
      "integrationToken",
      "organizationId",
      "senderEmail",
      "receiverEmail",
      "receiverName",
      "intent",
      "draftMeta",
      "refreshDraftButton",
      "livePreviewToggle",
      "previewButton",
      "analyzeButton",
      "applyButton",
      "debugToggleButton",
      "debugRefreshButton",
      "debugClearButton",
      "debugPanel",
      "debugOutput",
      "statusText",
      "resultRegion",
    ].forEach((id) => {
      fields[id] = document.getElementById(id);
    });
  }

  function bindEvents() {
    fields.refreshDraftButton.addEventListener("click", () => hydrateFromDraft(true));
    fields.livePreviewToggle.addEventListener("change", handleLivePreviewToggle);
    fields.previewButton.addEventListener("click", previewInlineSuggestions);
    fields.analyzeButton.addEventListener("click", analyzeDraft);
    fields.applyButton.addEventListener("click", applyImprovedVersion);
    fields.debugToggleButton.addEventListener("click", toggleDebugPanel);
    fields.debugRefreshButton.addEventListener("click", refreshDebugPanel);
    fields.debugClearButton.addEventListener("click", clearDebugPanel);
    [
      "backendUrl",
      "integrationToken",
      "organizationId",
      "senderEmail",
      "receiverEmail",
      "receiverName",
      "intent",
    ].forEach((id) => {
      fields[id].addEventListener("change", saveSettings);
    });
    fields.resultRegion.addEventListener("click", handleResultClick);
  }

  function restoreSettings() {
    let settings = {};
    try {
      settings = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (error) {
      settings = {};
    }

    fields.backendUrl.value = settings.backendUrl || "";
    fields.integrationToken.value = settings.integrationToken || "";
    fields.organizationId.value = settings.organizationId || "1";
    fields.senderEmail.value = settings.senderEmail || "";
    fields.receiverEmail.value = settings.receiverEmail || "";
    fields.receiverName.value = settings.receiverName || "";
    fields.intent.value = settings.intent || "request";
    fields.livePreviewToggle.checked = Boolean(settings.livePreviewEnabled);
    state.livePreviewEnabled = fields.livePreviewToggle.checked;
    state.debugEnabled = Boolean(settings.debugEnabled);
    applyDebugVisibility();
  }

  function saveSettings() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      backendUrl: fields.backendUrl.value.trim(),
      integrationToken: fields.integrationToken.value,
      organizationId: fields.organizationId.value.trim(),
      senderEmail: fields.senderEmail.value.trim(),
      receiverEmail: fields.receiverEmail.value.trim(),
      receiverName: fields.receiverName.value.trim(),
      intent: fields.intent.value,
      livePreviewEnabled: fields.livePreviewToggle.checked,
      debugEnabled: state.debugEnabled,
    }));
  }

  async function hydrateFromDraft(announce) {
    try {
      const draft = await readDraft();
      const firstRecipient = draft.toRecipients[0] || {};

      if (!fields.senderEmail.value && draft.senderEmail) {
        fields.senderEmail.value = draft.senderEmail;
      }
      if (!fields.receiverEmail.value && firstRecipient.emailAddress) {
        fields.receiverEmail.value = firstRecipient.emailAddress;
      }
      if (!fields.receiverName.value && firstRecipient.displayName) {
        fields.receiverName.value = firstRecipient.displayName;
      }

      saveSettings();
      renderDraftMeta(draft);
      state.lastObservedBody = draft.body || "";
      if (announce) setStatus("Draft details refreshed.");
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  async function analyzeDraft() {
    fields.analyzeButton.disabled = true;
    fields.applyButton.disabled = true;
    setStatus("Reading Outlook draft...");
    clearResults();

    try {
      const draft = await readDraft();
      const payload = buildPayload(draft);
      saveSettings();
      renderDraftMeta(draft);

      setStatus("Running receiver-aware analysis...");
      const data = await postJson(ENDPOINTS.analyze, payload);
      state.lastAnalysis = data;
      state.lastImprovedVersion = data.improved_version || "";
      renderAnalysis(data);
      fields.applyButton.disabled = !state.lastImprovedVersion;
      setStatus("Analysis complete. Review before applying changes.");
    } catch (error) {
      state.lastAnalysis = null;
      state.lastImprovedVersion = "";
      setStatus(error.message, true);
    } finally {
      fields.analyzeButton.disabled = false;
    }
  }

  async function previewInlineSuggestions() {
    fields.previewButton.disabled = true;
    fields.applyButton.disabled = true;
    setStatus("Reading Outlook draft...");
    clearResults();

    try {
      await runPreviewForCurrentDraft({ manual: true });
    } catch (error) {
      state.previewSuggestions = [];
      setStatus(error.message, true);
    } finally {
      fields.previewButton.disabled = false;
    }
  }

  function handleLivePreviewToggle() {
    state.livePreviewEnabled = fields.livePreviewToggle.checked;
    saveSettings();

    if (state.livePreviewEnabled) {
      startLivePreview();
      setStatus("Live preview enabled. Suggestions will update automatically.");
    } else {
      stopLivePreview();
      setStatus("Live preview disabled.");
    }
  }

  function startLivePreview() {
    stopLivePreview();
    state.livePreviewPollTimer = setInterval(pollDraftForLivePreview, LIVE_PREVIEW_POLL_MS);
    pollDraftForLivePreview();
  }

  function stopLivePreview() {
    if (state.livePreviewPollTimer) {
      clearInterval(state.livePreviewPollTimer);
      state.livePreviewPollTimer = null;
    }
    if (state.livePreviewDebounceTimer) {
      clearTimeout(state.livePreviewDebounceTimer);
      state.livePreviewDebounceTimer = null;
    }
  }

  async function pollDraftForLivePreview() {
    if (!state.livePreviewEnabled || state.livePreviewRequestInFlight) return;

    try {
      const draft = await readDraft();
      renderDraftMeta(draft);
      const body = draft.body || "";
      if (body === state.lastObservedBody) return;

      state.lastObservedBody = body;
      if (body.trim().length < MIN_PREVIEW_BODY_LENGTH) {
        clearTimeout(state.livePreviewDebounceTimer);
        state.previewSuggestions = [];
        state.lastPreviewedBody = "";
        renderPreview({ suggestions: [] }, []);
        setStatus(`Live preview waits for at least ${MIN_PREVIEW_BODY_LENGTH} characters.`);
        return;
      }

      clearTimeout(state.livePreviewDebounceTimer);
      state.livePreviewDebounceTimer = setTimeout(() => {
        runPreviewForCurrentDraft({ manual: false });
      }, LIVE_PREVIEW_DEBOUNCE_MS);
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  function scheduleLivePreviewRefresh(reason, delayMs = 250) {
    if (!state.livePreviewEnabled) return;

    if (state.livePreviewRequestInFlight) {
      state.pendingPreviewRefreshReason = reason;
      addDebugEvent("preview_refresh_pending", { reason });
      return;
    }

    clearTimeout(state.livePreviewDebounceTimer);
    state.livePreviewDebounceTimer = setTimeout(() => {
      runPreviewForCurrentDraft({ manual: false, forceFullBody: true });
    }, delayMs);
    addDebugEvent("preview_scheduled", { reason });
  }

  async function runPreviewForCurrentDraft({ manual, forceFullBody = false }) {
    if (state.livePreviewRequestInFlight) return;
    state.livePreviewRequestInFlight = true;

    try {
      const draft = await readDraft();
      if (draft.body.trim().length < MIN_PREVIEW_BODY_LENGTH) {
        state.previewSuggestions = [];
        renderPreview({ suggestions: [] }, []);
        setStatus(`Type at least ${MIN_PREVIEW_BODY_LENGTH} characters for inline suggestions.`);
        return;
      }

      const payload = buildPayload(draft);
      payload.full_draft = draft.body;
      const changed = previewChangedText(forceFullBody ? "" : state.lastPreviewedBody, draft.body, manual || forceFullBody);
      payload.changed_text = changed.text;
      payload.surrounding_context = previewSurroundingContext(draft, changed);
      payload.prior_review_context = state.dismissedSuggestionContext;
      delete payload.body;
      delete payload.subject;

      if (!payload.changed_text.trim()) return;
      if (!manual && !isReviewablePreviewChange(draft.body, changed)) {
        setStatus("Waiting for a complete phrase before checking...");
        return;
      }

      saveSettings();
      renderDraftMeta(draft);
      setStatus(manual ? "Checking inline suggestions..." : "Live preview checking suggestions...");
      const requestId = ++state.previewRequestId;
      const mutationVersion = state.bodyMutationVersion;
      const data = await postJson(ENDPOINTS.preview, payload);
      const currentDraft = await readDraft();
      if (
        requestId !== state.previewRequestId
        || mutationVersion !== state.bodyMutationVersion
      ) {
        addDebugEvent("preview_discarded", {
          manual,
          request_id: requestId,
          latest_request_id: state.previewRequestId,
          request_body_length: draft.body.length,
          current_body_length: currentDraft.body.length,
          body_changed_during_request: currentDraft.body !== draft.body,
          mutation_version_at_request: mutationVersion,
          current_mutation_version: state.bodyMutationVersion,
        });
        return;
      }

      const incomingSuggestions = normalizePreviewSuggestions(data.suggestions || [], draft.body)
        .map((suggestion) => {
          const range = reanchorSuggestionRange(suggestion, currentDraft.body, suggestion.range);
          return range ? { ...suggestion, range } : null;
        })
        .filter((suggestion) => suggestion);
      state.previewSuggestions = mergePreviewSuggestions(state.previewSuggestions, incomingSuggestions, currentDraft.body);
      addDebugEvent("preview_response", {
        manual,
        body_length: currentDraft.body.length,
        request_body_length: draft.body.length,
        body_changed_during_request: currentDraft.body !== draft.body,
        changed_text: payload.changed_text,
        changed_text_length: payload.changed_text.length,
        changed_range: { start: changed.start, end: changed.end },
        backend_text_hash: data.text_hash || "",
        raw_suggestion_count: (data.suggestions || []).length,
        incoming_suggestion_count: incomingSuggestions.length,
        visible_suggestion_count: state.previewSuggestions.length,
        incoming_suggestions: debugSuggestionRows(incomingSuggestions, currentDraft.body),
        suggestions: debugSuggestionRows(state.previewSuggestions, currentDraft.body),
      });
      state.lastPreviewedBody = draft.body;
      renderPreview(data, state.previewSuggestions);
      setStatus(state.previewSuggestions.length ? `${state.previewSuggestions.length} suggestion(s) ready.` : "No inline suggestions returned.");
    } catch (error) {
      if (manual) state.previewSuggestions = [];
      addDebugEvent("preview_error", {
        manual,
        error: error.message,
      });
      setStatus(error.message, true);
    } finally {
      state.livePreviewRequestInFlight = false;
      const pendingPreviewRefreshReason = state.pendingPreviewRefreshReason;
      state.pendingPreviewRefreshReason = null;
      if (pendingPreviewRefreshReason) {
        scheduleLivePreviewRefresh(pendingPreviewRefreshReason, 0);
      }
    }
  }

  async function applyImprovedVersion() {
    if (!state.lastImprovedVersion) {
      setStatus("No improved version is available yet.", true);
      return;
    }

    try {
      const bodyText = bodyTextForApply(state.lastImprovedVersion);
      await setDraftBody(bodyText);
      setStatus("Improved version applied to the draft body. Outlook has not sent the email.");
      logOutlookEvent("outlook_improved_version_applied");
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  async function handleResultClick(event) {
    const applyButton = event.target.closest("[data-apply-suggestion]");
    if (applyButton) {
      event.preventDefault();
      await applyPreviewSuggestion(applyButton.dataset.applySuggestion);
      return;
    }

    const dismissButton = event.target.closest("[data-dismiss-suggestion]");
    if (dismissButton) {
      event.preventDefault();
      dismissPreviewSuggestion(dismissButton.dataset.dismissSuggestion);
    }
  }

  async function applyPreviewSuggestion(suggestionId) {
    const suggestion = state.previewSuggestions.find((item) => item.local_id === suggestionId);
    if (!suggestion) return;

    try {
      const draft = await readDraft();
      addDebugEvent("apply_attempt", {
        suggestion_id: suggestion.id || suggestion.local_id,
        local_id: suggestion.local_id,
        body_length: draft.body.length,
        target_text: suggestion.target_text,
        range: suggestion.range || null,
        range_slice: sliceRange(draft.body, suggestion.range),
        range_matches_target: rangeMatchesTarget(draft.body, suggestion),
        nearest_target_index: nearestTargetIndex(draft.body, suggestion.target_text),
        visible_suggestions_before: debugSuggestionRows(state.previewSuggestions, draft.body),
      });
      const range = resolveSuggestionRange(suggestion, draft.body);
      const updatedBody = replaceDraftRange(draft.body, range, suggestion.suggested_replacement);
      await setDraftBody(updatedBody);
      state.bodyMutationVersion += 1;
      state.previewRequestId += 1;
      const postApplyDraft = await readDraft();
      state.lastObservedBody = postApplyDraft.body;
      state.lastPreviewedBody = postApplyDraft.body;
      state.previewSuggestions = updateSuggestionsAfterApply(
        state.previewSuggestions,
        suggestion,
        range,
        suggestion.suggested_replacement,
        postApplyDraft.body,
      );
      addDebugEvent("apply_success", {
        suggestion_id: suggestion.id || suggestion.local_id,
        local_id: suggestion.local_id,
        applied_range: range,
        replacement_length: String(suggestion.suggested_replacement || "").length,
        updated_body_length: updatedBody.length,
        post_apply_body_length: postApplyDraft.body.length,
        post_apply_body_preview: previewText(postApplyDraft.body, 700),
        visible_suggestions_after: debugSuggestionRows(state.previewSuggestions, postApplyDraft.body),
      });
      renderPreview({ suggestions: state.previewSuggestions }, state.previewSuggestions);
      scheduleLivePreviewRefresh("post_apply");
      setStatus("Suggestion applied to the draft body. Outlook has not sent the email.");
      logOutlookEvent("outlook_suggestion_applied", {
        suggestion_id: suggestion.id || suggestion.local_id,
        target_text: suggestion.target_text,
      });
    } catch (error) {
      addDebugEvent("apply_error", {
        local_id: suggestion.local_id,
        target_text: suggestion.target_text,
        error: error.message,
      });
      setStatus(error.message, true);
    }
  }

  function dismissPreviewSuggestion(suggestionId) {
    const suggestion = state.previewSuggestions.find((item) => item.local_id === suggestionId);
    if (!suggestion) return;

    state.dismissedSuggestionKeys.push(suggestionKey(suggestion));
    state.dismissedSuggestionContext.push({
      id: suggestion.local_id,
      status: "dismissed",
      text: suggestion.target_text,
      text_hash: suggestionKey(suggestion),
      suggestions: [{
        target_text: suggestion.target_text,
        suggested_replacement: suggestion.suggested_replacement,
        issue: suggestion.issue || "",
        reason: suggestion.reason || "",
      }],
    });
    state.dismissedSuggestionContext = state.dismissedSuggestionContext.slice(-10);
    addDebugEvent("dismiss", {
      suggestion_id: suggestion.id || suggestion.local_id,
      local_id: suggestion.local_id,
      target_text: suggestion.target_text,
      range: suggestion.range || null,
    });
    state.previewSuggestions = state.previewSuggestions.filter((item) => item.local_id !== suggestionId);
    renderPreview({ suggestions: state.previewSuggestions }, state.previewSuggestions);
    scheduleLivePreviewRefresh("post_dismiss");
    setStatus("Suggestion dismissed.");
    logOutlookEvent("outlook_suggestion_rejected", {
      suggestion_id: suggestion.id || suggestion.local_id,
      target_text: suggestion.target_text,
    });
  }

  function buildPayload(draft) {
    const firstRecipient = draft.toRecipients[0] || {};
    const payload = {
      organization_id: fields.organizationId.value.trim(),
      sender_email: fields.senderEmail.value.trim() || draft.senderEmail,
      receiver_email: fields.receiverEmail.value.trim() || firstRecipient.emailAddress || "",
      receiver_name: fields.receiverName.value.trim() || firstRecipient.displayName || "",
      subject: draft.subject,
      body: draft.body,
      intent: fields.intent.value,
      channel: "outlook",
    };

    if (!payload.organization_id) throw new Error("Organization ID is required.");
    if (!payload.sender_email) throw new Error("Sender email is required.");
    if (!payload.receiver_email) throw new Error("Receiver email is required.");
    if (!payload.intent) throw new Error("Intent is required.");
    if (!payload.body || !payload.body.trim()) throw new Error("Draft body is required.");
    if (!fields.backendUrl.value.trim()) throw new Error("Backend URL is required.");
    if (!fields.integrationToken.value) throw new Error("Integration token is required.");

    fields.senderEmail.value = payload.sender_email;
    fields.receiverEmail.value = payload.receiver_email;
    fields.receiverName.value = payload.receiver_name;
    return payload;
  }

  async function readDraft() {
    const item = getComposeItem();
    const senderEmail = getSenderEmail();
    const [subject, body, toRecipients] = await Promise.all([
      getSubject(item),
      getBody(item),
      getToRecipients(item),
    ]);

    return {
      subject: subject || "",
      body: body || "",
      toRecipients,
      senderEmail,
    };
  }

  function getComposeItem() {
    if (!state.officeReady || !Office.context || !Office.context.mailbox || !Office.context.mailbox.item) {
      throw new Error("Open this task pane from an Outlook compose draft.");
    }
    return Office.context.mailbox.item;
  }

  function getSenderEmail() {
    const profile = Office.context && Office.context.mailbox && Office.context.mailbox.userProfile;
    return profile && profile.emailAddress ? profile.emailAddress : "";
  }

  function getSubject(item) {
    return new Promise((resolve, reject) => {
      if (!item.subject || !item.subject.getAsync) {
        resolve("");
        return;
      }
      item.subject.getAsync((result) => {
        if (result.status === Office.AsyncResultStatus.Succeeded) {
          resolve(result.value || "");
        } else {
          reject(new Error(result.error && result.error.message ? result.error.message : "Could not read subject."));
        }
      });
    });
  }

  function getBody(item) {
    return new Promise((resolve, reject) => {
      item.body.getAsync(Office.CoercionType.Text, (result) => {
        if (result.status === Office.AsyncResultStatus.Succeeded) {
          resolve(result.value || "");
        } else {
          reject(new Error(result.error && result.error.message ? result.error.message : "Could not read draft body."));
        }
      });
    });
  }

  function getToRecipients(item) {
    return new Promise((resolve, reject) => {
      if (!item.to || !item.to.getAsync) {
        resolve([]);
        return;
      }
      item.to.getAsync((result) => {
        if (result.status === Office.AsyncResultStatus.Succeeded) {
          resolve((result.value || []).map(normalizeRecipient).filter((recipient) => recipient.emailAddress));
        } else {
          reject(new Error(result.error && result.error.message ? result.error.message : "Could not read recipients."));
        }
      });
    });
  }

  function normalizeRecipient(recipient) {
    return {
      emailAddress: recipient.emailAddress || recipient.address || "",
      displayName: recipient.displayName || recipient.name || "",
    };
  }

  function setDraftBody(text) {
    const item = getComposeItem();
    return new Promise((resolve, reject) => {
      item.body.setAsync(text, { coercionType: Office.CoercionType.Text }, (result) => {
        if (result.status === Office.AsyncResultStatus.Succeeded) {
          resolve();
        } else {
          reject(new Error(result.error && result.error.message ? result.error.message : "Could not update draft body."));
        }
      });
    });
  }

  async function postJson(path, payload) {
    const backendUrl = trimTrailingSlash(fields.backendUrl.value.trim());
    const response = await fetch(`${backendUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Outlook-Integration-Token": fields.integrationToken.value,
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify(payload),
    });

    const text = await response.text();
    const data = parseBackendJson(text, response.status);
    if (!response.ok) {
      throw new Error(data.error || `Backend returned HTTP ${response.status}`);
    }
    return data;
  }

  async function logOutlookEvent(eventType, extraPayload) {
    const analysis = state.lastAnalysis || {};

    try {
      await postJson(ENDPOINTS.event, {
        event_type: eventType,
        message_id: analysis.message_id,
        organization_id: analysis.organization_id || fields.organizationId.value.trim(),
        sender_email: fields.senderEmail.value.trim(),
        receiver_email: fields.receiverEmail.value.trim(),
        ...(extraPayload || {}),
      });
    } catch (error) {
      console.warn("Could not log Outlook event", error);
    }
  }

  function renderDraftMeta(draft) {
    const firstRecipient = draft.toRecipients[0];
    const recipient = firstRecipient
      ? `${firstRecipient.displayName || firstRecipient.emailAddress} <${firstRecipient.emailAddress}>`
      : "No To recipient";
    const subject = draft.subject ? draft.subject : "No subject";
    const bodyLength = draft.body ? draft.body.trim().length : 0;
    fields.draftMeta.textContent = `${subject} | To: ${recipient} | Body: ${bodyLength} chars`;
  }

  function renderAnalysis(data) {
    clearResults();
    appendCard("Scores", renderScores(data.scores || {}));
    appendCard("Improved version", data.improved_version || "No improved version returned.", true);
    appendCard("Short version", data.short_version || "No short version returned.", true);
    appendCard("Explanation", data.explanation || "No explanation returned.", true);

    if (Array.isArray(data.suggestions) && data.suggestions.length) {
      appendCard("Suggestions", renderSuggestions(data.suggestions));
    }

    const dashboardUrl = data.dashboard_absolute_url || data.dashboard_url;
    if (dashboardUrl) {
      const link = document.createElement("a");
      link.href = dashboardUrl;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Open dashboard";
      appendCard("Dashboard", link);
    }
  }

  function renderPreview(data, suggestions) {
    clearResults();
    appendCard("Inline suggestions", renderSuggestions(suggestions || data.suggestions || [], true));
  }

  function renderScores(scores) {
    const root = document.createElement("div");
    root.className = "score-grid";
    const current = scores.current || scores;
    const keys = Object.keys(current || {});
    if (!keys.length) {
      root.textContent = "No scores returned.";
      return root;
    }

    keys.forEach((key) => {
      const row = document.createElement("div");
      row.className = "score-row";
      const label = document.createElement("span");
      label.className = "score-label";
      label.textContent = key.replace(/_/g, " ");
      const value = document.createElement("strong");
      value.textContent = String(current[key]);
      row.append(label, value);
      root.appendChild(row);
    });
    return root;
  }

  function renderSuggestions(suggestions, withActions) {
    const list = document.createElement("ul");
    list.className = "suggestions-list";
    suggestions.forEach((suggestion) => {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = suggestion.issue || "Suggestion";
      const replacement = document.createElement("pre");
      replacement.textContent = suggestion.suggested_replacement || "";
      const reason = document.createElement("p");
      reason.textContent = suggestion.reason || "";
      item.append(title, replacement, reason);

      if (withActions) {
        const target = document.createElement("p");
        target.className = "suggestion-target";
        target.textContent = `Target: ${suggestion.target_text || ""}`;
        const actions = document.createElement("div");
        actions.className = "suggestion-actions";
        const apply = document.createElement("button");
        apply.type = "button";
        apply.className = "primary";
        apply.dataset.applySuggestion = suggestion.local_id;
        apply.textContent = "Apply";
        const dismiss = document.createElement("button");
        dismiss.type = "button";
        dismiss.className = "secondary";
        dismiss.dataset.dismissSuggestion = suggestion.local_id;
        dismiss.textContent = "Dismiss";
        actions.append(apply, dismiss);
        item.append(target, actions);
      }

      list.appendChild(item);
    });
    if (!suggestions.length) {
      const item = document.createElement("li");
      item.textContent = "No inline suggestions returned.";
      list.appendChild(item);
    }
    return list;
  }

  function appendCard(title, content, preserveWhitespace) {
    const card = document.createElement("article");
    card.className = "result-card";
    const heading = document.createElement("h2");
    heading.textContent = title;
    card.appendChild(heading);

    if (content instanceof Node) {
      card.appendChild(content);
    } else if (preserveWhitespace) {
      const pre = document.createElement("pre");
      pre.textContent = content;
      card.appendChild(pre);
    } else {
      const paragraph = document.createElement("p");
      paragraph.textContent = content;
      card.appendChild(paragraph);
    }

    fields.resultRegion.appendChild(card);
  }

  function clearResults() {
    fields.resultRegion.replaceChildren();
  }

  function setStatus(message, isError) {
    fields.statusText.textContent = message;
    fields.statusText.classList.toggle("error", Boolean(isError));
  }

  function toggleDebugPanel() {
    state.debugEnabled = !state.debugEnabled;
    saveSettings();
    applyDebugVisibility();
    if (state.debugEnabled) {
      refreshDebugPanel();
    }
  }

  function applyDebugVisibility() {
    fields.debugPanel.hidden = !state.debugEnabled;
    fields.debugRefreshButton.hidden = !state.debugEnabled;
    fields.debugClearButton.hidden = !state.debugEnabled;
    fields.debugToggleButton.textContent = state.debugEnabled ? "Hide Debug" : "Show Debug";
    fields.debugToggleButton.setAttribute("aria-expanded", state.debugEnabled ? "true" : "false");
  }

  async function refreshDebugPanel() {
    if (!state.debugEnabled) return;

    let snapshot = {};
    try {
      const draft = await readDraft();
      snapshot = {
        current_body_length: draft.body.length,
        current_body_preview: previewText(draft.body, 700),
        last_observed_body_length: state.lastObservedBody.length,
        last_previewed_body_length: state.lastPreviewedBody.length,
        visible_suggestions: debugSuggestionRows(state.previewSuggestions, draft.body),
      };
    } catch (error) {
      snapshot = { error: error.message };
    }

    fields.debugOutput.textContent = JSON.stringify({
      snapshot,
      events: state.debugEvents.slice(-20),
    }, null, 2);
  }

  function clearDebugPanel() {
    state.debugEvents = [];
    refreshDebugPanel();
  }

  function addDebugEvent(type, details) {
    if (!state.debugEnabled) return;
    state.debugEvents.push({
      at: new Date().toISOString(),
      type,
      ...safeDebugValue(details || {}),
    });
    state.debugEvents = state.debugEvents.slice(-50);
    refreshDebugPanel().catch(() => {});
  }

  function safeDebugValue(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function debugSuggestionRows(suggestions, body) {
    const draft = String(body || "");
    return (suggestions || []).map((suggestion, index) => ({
      index,
      local_id: suggestion.local_id || "",
      backend_id: suggestion.id || null,
      issue: suggestion.issue || "",
      target_text: suggestion.target_text || "",
      replacement: suggestion.suggested_replacement || "",
      range: suggestion.range || null,
      range_slice: sliceRange(draft, suggestion.range),
      range_matches_target: rangeMatchesTarget(draft, suggestion),
      nearest_target_index: nearestTargetIndex(draft, suggestion.target_text),
      target_occurrence_count: targetOccurrenceCount(draft, suggestion.target_text),
    }));
  }

  function rangeMatchesTarget(body, suggestion) {
    return Boolean(
      suggestion
      && suggestion.range
      && String(body || "").slice(suggestion.range.start, suggestion.range.end) === String(suggestion.target_text || "")
    );
  }

  function sliceRange(body, range) {
    if (!range) return "";
    return String(body || "").slice(range.start, range.end);
  }

  function nearestTargetIndex(body, targetText) {
    const range = findNearestTargetRange(body, targetText, 0);
    return range ? range.start : -1;
  }

  function targetOccurrenceCount(body, targetText) {
    const draft = String(body || "");
    const target = String(targetText || "");
    if (!target) return 0;
    let count = 0;
    let index = draft.indexOf(target);
    while (index >= 0) {
      count += 1;
      index = draft.indexOf(target, index + target.length);
    }
    return count;
  }

  function previewText(text, maxLength) {
    const value = String(text || "");
    return value.length <= maxLength ? value : `${value.slice(0, maxLength)}...`;
  }

  function bodyTextForApply(text) {
    const match = String(text || "").match(/^Subject:\s*[^\r\n]*(?:\r?\n){2,}([\s\S]*)$/i);
    return (match ? match[1] : text).trim();
  }

  function changedText(previousBody, currentBody) {
    const previous = previousBody || "";
    const current = currentBody || "";
    if (!previous || !current.startsWith(previous)) return current.trim();
    return current.slice(previous.length).trim() || current.trim();
  }

  function previewChangedText(previousBody, currentBody, manual) {
    const current = String(currentBody || "");
    if (manual || !previousBody) {
      const start = firstNonWhitespaceIndex(current);
      const end = current.trimEnd().length;
      return {
        text: current.slice(start, end).trim(),
        start,
        end,
      };
    }

    const diff = changedRange(String(previousBody || ""), current);
    if (diff.start === diff.end) return { text: "", start: diff.start, end: diff.end };

    let start = diff.start;
    let end = diff.end;

    while (start > 0 && !isPreviewBoundary(current[start - 1])) {
      start -= 1;
    }
    while (end < current.length && !isPreviewBoundary(current[end])) {
      end += 1;
    }
    if (end < current.length && /[.!?]/.test(current[end])) {
      end += 1;
    }

    const raw = current.slice(start, end);
    const leadingWhitespace = raw.search(/\S/);
    const normalizedStart = leadingWhitespace < 0 ? start : start + leadingWhitespace;
    const normalizedEnd = start + raw.trimEnd().length;
    return {
      text: current.slice(normalizedStart, normalizedEnd).trim(),
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

  function firstNonWhitespaceIndex(text) {
    const index = String(text || "").search(/\S/);
    return index < 0 ? 0 : index;
  }

  function isPreviewBoundary(char) {
    return /[.!?\n\r]/.test(char);
  }

  function isReviewablePreviewChange(fullDraft, changed) {
    const text = String(changed?.text || "").trim();
    if (text.length < MIN_PREVIEW_BODY_LENGTH) return false;
    if (endsInsideShortWord(fullDraft, changed)) return false;
    return true;
  }

  function endsInsideShortWord(fullDraft, changed) {
    const draft = String(fullDraft || "");
    const draftEnd = draft.trimEnd().length;
    if ((changed?.end ?? 0) !== draftEnd) return false;
    const reviewedText = draft.slice(changed.start, changed.end).trimEnd();
    return /[A-Za-z]{1,2}$/.test(reviewedText);
  }

  function previewSurroundingContext(draft, changed) {
    const subject = draft.subject ? `Subject: ${draft.subject}\n\n` : "";
    const body = String(draft.body || "");
    const start = Math.max(0, (changed?.start ?? body.length) - 240);
    const end = Math.min(body.length, (changed?.end ?? body.length) + 240);
    return `${subject}${body.slice(start, end)}`;
  }

  function normalizePreviewSuggestions(suggestions, body) {
    const draft = String(body || "");
    return suggestions
      .filter((suggestion) => suggestion && suggestion.target_text && suggestion.suggested_replacement)
      .map((suggestion, index) => ({
        ...suggestion,
        local_id: `suggestion-${Date.now()}-${index}`,
        range: resolveInitialSuggestionRange(suggestion, draft),
      }))
      .filter((suggestion) => suggestion.range && !state.dismissedSuggestionKeys.includes(suggestionKey(suggestion)));
  }

  function resolveInitialSuggestionRange(suggestion, body) {
    const target = String(suggestion.target_text || "");
    if (!target) return null;
    return findNearestTargetRange(body, target, 0);
  }

  function resolveSuggestionRange(suggestion, body) {
    const draft = String(body || "");
    const range = suggestion.range;
    if (range && draft.slice(range.start, range.end) === suggestion.target_text) {
      return range;
    }

    const target = String(suggestion.target_text || "");
    if (!target) throw new Error("Suggestion target is missing.");
    const resolved = findNearestTargetRange(draft, target, range?.start ?? 0);
    if (!resolved) {
      throw new Error("Suggestion target no longer matches the draft. Refresh suggestions and try again.");
    }
    return resolved;
  }

  function replaceDraftRange(body, range, replacementText) {
    if (!range) throw new Error("Suggestion target no longer matches the draft. Refresh suggestions and try again.");
    return `${body.slice(0, range.start)}${replacementText}${body.slice(range.end)}`;
  }

  function updateSuggestionsAfterApply(suggestions, acceptedSuggestion, acceptedRange, replacementText, currentBody) {
    const delta = String(replacementText || "").length - (acceptedRange.end - acceptedRange.start);
    return suggestions
      .filter((suggestion) => suggestion.local_id !== acceptedSuggestion.local_id)
      .filter((suggestion) => suggestion.range && !rangesOverlap(suggestion.range, acceptedRange))
      .map((suggestion) => {
        let nextRange = suggestion.range;
        if (suggestion.range.start >= acceptedRange.end) {
          nextRange = {
            start: suggestion.range.start + delta,
            end: suggestion.range.end + delta,
          };
        }
        const anchoredRange = reanchorSuggestionRange(suggestion, currentBody, nextRange);
        if (!anchoredRange) return null;
        return {
          ...suggestion,
          range: anchoredRange,
        };
      })
      .filter((suggestion) => suggestion);
  }

  function mergePreviewSuggestions(existingSuggestions, incomingSuggestions, currentBody) {
    const merged = [];
    const seen = new Set();
    const body = String(currentBody || "");

    [...existingSuggestions, ...incomingSuggestions].forEach((suggestion) => {
      const range = reanchorSuggestionRange(suggestion, body, suggestion.range);
      if (!range) return;

      const nextSuggestion = { ...suggestion, range };
      const key = suggestionMergeKey(nextSuggestion);
      if (seen.has(key)) return;

      seen.add(key);
      merged.push(nextSuggestion);
    });

    return merged.sort((a, b) => (
      (a.range?.start ?? 0) - (b.range?.start ?? 0)
      || String(a.local_id || "").localeCompare(String(b.local_id || ""))
    ));
  }

  function suggestionMergeKey(suggestion) {
    const range = suggestion.range || {};
    return [
      suggestionKey(suggestion),
      range.start ?? "",
      range.end ?? "",
    ].join("\u0001");
  }

  function rangesOverlap(a, b) {
    return a.start < b.end && b.start < a.end;
  }

  function reanchorSuggestionRange(suggestion, body, preferredRange) {
    const draft = String(body || "");
    if (
      preferredRange
      && draft.slice(preferredRange.start, preferredRange.end) === String(suggestion.target_text || "")
    ) {
      return preferredRange;
    }
    return findNearestTargetRange(draft, suggestion.target_text, preferredRange?.start ?? 0);
  }

  function findNearestTargetRange(body, targetText, preferredStart) {
    const draft = String(body || "");
    const target = String(targetText || "");
    if (!target) return null;

    const exact = findNearestMatch(draft, target, preferredStart, false);
    if (exact) return exact;

    const insensitive = findNearestMatch(draft, target, preferredStart, true);
    if (insensitive) return insensitive;

    return findNearestFlexibleWhitespaceRange(draft, target, preferredStart);
  }

  function findNearestMatch(body, targetText, preferredStart, ignoreCase) {
    const haystack = ignoreCase ? body.toLowerCase() : body;
    const needle = ignoreCase ? targetText.toLowerCase() : targetText;
    let best = null;
    let bestDistance = Number.MAX_SAFE_INTEGER;
    let index = haystack.indexOf(needle);
    while (index >= 0) {
      const distance = Math.abs(index - preferredStart);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = { start: index, end: index + targetText.length };
      }
      index = haystack.indexOf(needle, index + 1);
    }
    return best;
  }

  function findNearestFlexibleWhitespaceRange(body, targetText, preferredStart) {
    const tokens = String(targetText || "").trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) return null;

    const matcher = new RegExp(tokens.map(escapeRegExp).join("\\s+"), "gi");
    let best = null;
    let bestDistance = Number.MAX_SAFE_INTEGER;
    let match = matcher.exec(body);
    while (match) {
      const distance = Math.abs(match.index - preferredStart);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = { start: match.index, end: match.index + match[0].length };
      }
      match = matcher.exec(body);
    }
    return best;
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function suggestionKey(suggestion) {
    return `${suggestion.target_text || ""}::${suggestion.suggested_replacement || ""}`;
  }

  function trimTrailingSlash(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function parseBackendJson(text, status) {
    try {
      return JSON.parse(text || "{}");
    } catch (error) {
      const body = String(text || "").trim();
      if (/^\s*<!doctype html|^\s*<html/i.test(body)) {
        return { error: `Backend returned HTML instead of JSON (HTTP ${status}).` };
      }
      return { error: body || `Backend returned HTTP ${status}` };
    }
  }
})();
