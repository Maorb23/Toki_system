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
    livePreviewEnabled: false,
    livePreviewPollTimer: null,
    livePreviewDebounceTimer: null,
    livePreviewRequestInFlight: false,
    lastObservedBody: "",
    lastPreviewedBody: "",
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

  async function runPreviewForCurrentDraft({ manual }) {
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
      payload.changed_text = changedText(state.lastPreviewedBody, draft.body);
      payload.surrounding_context = draft.subject ? `Subject: ${draft.subject}` : "";
      payload.prior_review_context = state.dismissedSuggestionKeys;
      delete payload.body;
      delete payload.subject;

      if (!payload.changed_text.trim()) return;

      saveSettings();
      renderDraftMeta(draft);
      setStatus(manual ? "Checking inline suggestions..." : "Live preview checking suggestions...");
      const data = await postJson(ENDPOINTS.preview, payload);
      state.previewSuggestions = normalizePreviewSuggestions(data.suggestions || []);
      state.lastPreviewedBody = draft.body;
      renderPreview(data, state.previewSuggestions);
      setStatus(state.previewSuggestions.length ? `${state.previewSuggestions.length} suggestion(s) ready.` : "No inline suggestions returned.");
    } catch (error) {
      if (manual) state.previewSuggestions = [];
      setStatus(error.message, true);
    } finally {
      state.livePreviewRequestInFlight = false;
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
      const updatedBody = replaceFirstDraftMatch(
        draft.body,
        suggestion.target_text,
        suggestion.suggested_replacement,
      );
      await setDraftBody(updatedBody);
      state.lastObservedBody = updatedBody;
      state.lastPreviewedBody = updatedBody;
      state.previewSuggestions = state.previewSuggestions.filter((item) => item.local_id !== suggestionId);
      renderPreview({ suggestions: state.previewSuggestions }, state.previewSuggestions);
      setStatus("Suggestion applied to the draft body. Outlook has not sent the email.");
      logOutlookEvent("outlook_suggestion_applied", {
        suggestion_id: suggestion.id || suggestion.local_id,
        target_text: suggestion.target_text,
      });
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  function dismissPreviewSuggestion(suggestionId) {
    const suggestion = state.previewSuggestions.find((item) => item.local_id === suggestionId);
    if (!suggestion) return;

    state.dismissedSuggestionKeys.push(suggestionKey(suggestion));
    state.previewSuggestions = state.previewSuggestions.filter((item) => item.local_id !== suggestionId);
    renderPreview({ suggestions: state.previewSuggestions }, state.previewSuggestions);
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

  function normalizePreviewSuggestions(suggestions) {
    return suggestions
      .filter((suggestion) => suggestion && suggestion.target_text && suggestion.suggested_replacement)
      .map((suggestion, index) => ({
        ...suggestion,
        local_id: `suggestion-${Date.now()}-${index}`,
      }));
  }

  function replaceFirstDraftMatch(body, targetText, replacementText) {
    const target = String(targetText || "");
    if (!target) throw new Error("Suggestion target is missing.");
    const index = String(body || "").indexOf(target);
    if (index === -1) {
      throw new Error("Suggestion target no longer matches the draft. Refresh suggestions and try again.");
    }
    return `${body.slice(0, index)}${replacementText}${body.slice(index + target.length)}`;
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
