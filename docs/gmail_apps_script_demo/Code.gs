const DEFAULT_BACKEND_URL = "https://YOUR-DOMAIN.com";
const DEFAULT_ORGANIZATION_ID = "1";

const PROP_BACKEND_URL = "BACKEND_URL";
const PROP_TOKEN = "GMAIL_INTEGRATION_TOKEN";
const PROP_ORG_ID = "ORGANIZATION_ID";

function onHomepage(e) {
  return buildInputCard_();
}

function buildInputCard_() {
  const backendUrl = getProperty_(PROP_BACKEND_URL, DEFAULT_BACKEND_URL);
  const organizationId = getProperty_(PROP_ORG_ID, DEFAULT_ORGANIZATION_ID);
  const token = getProperty_(PROP_TOKEN, "");

  const section = CardService.newCardSection()
    .addWidget(CardService.newTextParagraph().setText("Analyze a Gmail-style draft with the receiver-aware Django backend."))
    .addWidget(CardService.newTextInput()
      .setFieldName("backend_url")
      .setTitle("Backend URL")
      .setHint("https://your-domain.example.com")
      .setValue(backendUrl))
    .addWidget(CardService.newTextInput()
      .setFieldName("integration_token")
      .setTitle("Integration token")
      .setHint("Stored in Script Properties or entered for this test")
      .setValue(token))
    .addWidget(CardService.newTextInput()
      .setFieldName("organization_id")
      .setTitle("Organization ID")
      .setValue(organizationId))
    .addWidget(CardService.newTextInput()
      .setFieldName("sender_email")
      .setTitle("Sender email")
      .setHint("sender@acme.test"))
    .addWidget(CardService.newTextInput()
      .setFieldName("receiver_email")
      .setTitle("Receiver email")
      .setHint("receiver@acme.test"))
    .addWidget(CardService.newTextInput()
      .setFieldName("receiver_name")
      .setTitle("Receiver name optional")
      .setHint("Dana Receiver"))
    .addWidget(CardService.newTextInput()
      .setFieldName("subject")
      .setTitle("Subject")
      .setHint("Project update"))
    .addWidget(CardService.newTextInput()
      .setFieldName("body")
      .setTitle("Body")
      .setMultiline(true)
      .setHint("Paste the draft body here."))
    .addWidget(CardService.newSelectionInput()
      .setType(CardService.SelectionInputType.DROPDOWN)
      .setFieldName("intent")
      .setTitle("Intent")
      .addItem("Request", "request", true)
      .addItem("Update", "update", false)
      .addItem("Feedback", "feedback", false)
      .addItem("Decision", "decision", false)
      .addItem("Escalation", "escalation", false)
      .addItem("Alignment", "alignment", false))
    .addWidget(CardService.newTextButton()
      .setText("Analyze with Receiver-Aware")
      .setTextButtonStyle(CardService.TextButtonStyle.FILLED)
      .setOnClickAction(CardService.newAction().setFunctionName("handleAnalyze")))
    .addWidget(CardService.newTextButton()
      .setText("Preview inline suggestions")
      .setOnClickAction(CardService.newAction().setFunctionName("handleInlinePreview")));

  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle("Receiver-Aware Gmail Demo"))
    .addSection(section)
    .build();
}

function handleAnalyze(e) {
  const config = readDraftForm_(e);
  const validationError = validateInputs_(
    config.backendUrl,
    config.token,
    config.organizationId,
    config.senderEmail,
    config.receiverEmail,
    config.body
  );
  if (validationError) {
    return updateCard_(buildErrorCard_(validationError));
  }

  saveIfPresent_(PROP_BACKEND_URL, config.backendUrl);
  saveIfPresent_(PROP_TOKEN, config.token);
  saveIfPresent_(PROP_ORG_ID, config.organizationId);

  const payload = draftPayload_(config);
  const endpoint = config.backendUrl + "/api/v1/integrations/gmail/analyze-draft/";
  return callBackend_(endpoint, config.token, payload, buildResultCard_);
}

function handleInlinePreview(e) {
  const config = readDraftForm_(e);
  const validationError = validateInputs_(
    config.backendUrl,
    config.token,
    config.organizationId,
    config.senderEmail,
    config.receiverEmail,
    config.body
  );
  if (validationError) {
    return updateCard_(buildErrorCard_(validationError));
  }

  saveIfPresent_(PROP_BACKEND_URL, config.backendUrl);
  saveIfPresent_(PROP_TOKEN, config.token);
  saveIfPresent_(PROP_ORG_ID, config.organizationId);

  const payload = draftPayload_(config);
  payload.full_draft = config.body;
  payload.changed_text = latestPreviewSpan_(config.body);
  payload.surrounding_context = config.body;

  if (!payload.changed_text) {
    return updateCard_(buildErrorCard_("Body is required for inline preview."));
  }

  const endpoint = config.backendUrl + "/api/v1/integrations/gmail/inline-suggestions/preview/";
  return callBackend_(endpoint, config.token, payload, buildInlinePreviewCard_);
}

function readDraftForm_(e) {
  const inputs = (e.commonEventObject && e.commonEventObject.formInputs) || {};
  return {
    backendUrl: trimTrailingSlash_(formValue_(inputs, "backend_url") || getProperty_(PROP_BACKEND_URL, DEFAULT_BACKEND_URL)),
    token: formValue_(inputs, "integration_token") || getProperty_(PROP_TOKEN, ""),
    organizationId: formValue_(inputs, "organization_id") || getProperty_(PROP_ORG_ID, DEFAULT_ORGANIZATION_ID),
    senderEmail: formValue_(inputs, "sender_email"),
    receiverEmail: formValue_(inputs, "receiver_email"),
    receiverName: formValue_(inputs, "receiver_name"),
    subject: formValue_(inputs, "subject"),
    body: formValue_(inputs, "body"),
    intent: formValue_(inputs, "intent") || "request",
  };
}

function draftPayload_(config) {
  return {
    organization_id: config.organizationId,
    sender_email: config.senderEmail,
    receiver_email: config.receiverEmail,
    receiver_name: config.receiverName,
    subject: config.subject,
    body: config.body,
    intent: config.intent,
  };
}

function callBackend_(endpoint, token, payload, successCardBuilder) {
  try {
    const response = UrlFetchApp.fetch(endpoint, {
      method: "post",
      contentType: "application/json",
      headers: {
        "X-Gmail-Integration-Token": token,
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
      followRedirects: true,
    });

    const statusCode = response.getResponseCode();
    const responseText = response.getContentText() || "{}";
    const data = parseJson_(responseText);
    if (statusCode < 200 || statusCode >= 300) {
      return updateCard_(buildErrorCard_(formatBackendError_(statusCode, data, responseText)));
    }
    return updateCard_(successCardBuilder(data));
  } catch (error) {
    return updateCard_(buildErrorCard_("Backend unreachable: " + error.message));
  }
}

function buildResultCard_(data) {
  const section = CardService.newCardSection();
  section.addWidget(CardService.newTextParagraph().setText("<b>Scores</b><br>" + formatScores_(data.scores && data.scores.current)));
  section.addWidget(CardService.newTextParagraph().setText("<b>Top suggestions</b><br>" + formatSuggestions_(data.suggestions || [])));
  section.addWidget(CardService.newTextParagraph().setText("<b>Improved version</b><br>" + escapeHtml_(data.improved_version || "No improved version returned.")));
  section.addWidget(CardService.newTextParagraph().setText("<b>Short version</b><br>" + escapeHtml_(data.short_version || "No short version returned.")));
  section.addWidget(CardService.newTextParagraph().setText("<b>Explanation</b><br>" + escapeHtml_(data.explanation || "No explanation returned.")));

  const dashboardUrl = data.dashboard_absolute_url || data.dashboard_url;
  if (dashboardUrl) {
    section.addWidget(CardService.newTextButton()
      .setText("Open Django dashboard")
      .setOpenLink(CardService.newOpenLink().setUrl(dashboardUrl)));
  }

  section.addWidget(CardService.newTextButton()
    .setText("Analyze another draft")
    .setOnClickAction(CardService.newAction().setFunctionName("resetCard")));

  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle("Receiver-Aware Results"))
    .addSection(section)
    .build();
}

function buildInlinePreviewCard_(data) {
  const section = CardService.newCardSection();
  section.addWidget(CardService.newTextParagraph().setText("<b>Inline suggestions</b><br>" + formatInlineSuggestions_(data.suggestions || [])));
  section.addWidget(CardService.newTextParagraph().setText("These suggestions are previews only. Copy a replacement manually into your Gmail draft if you want to use it."));
  section.addWidget(CardService.newTextButton()
    .setText("Back to draft form")
    .setOnClickAction(CardService.newAction().setFunctionName("resetCard")));

  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle("Receiver-Aware Inline Preview"))
    .addSection(section)
    .build();
}

function buildErrorCard_(message) {
  const section = CardService.newCardSection()
    .addWidget(CardService.newTextParagraph().setText("<b>Could not analyze draft</b><br>" + escapeHtml_(message)))
    .addWidget(CardService.newTextButton()
      .setText("Back")
      .setOnClickAction(CardService.newAction().setFunctionName("resetCard")));
  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle("Receiver-Aware Error"))
    .addSection(section)
    .build();
}

function resetCard() {
  return updateCard_(buildInputCard_());
}

function updateCard_(card) {
  return CardService.newActionResponseBuilder()
    .setNavigation(CardService.newNavigation().updateCard(card))
    .build();
}

function validateInputs_(backendUrl, token, organizationId, senderEmail, receiverEmail, body) {
  if (!backendUrl || backendUrl === DEFAULT_BACKEND_URL) return "Backend URL is missing. Set BACKEND_URL in Script Properties or enter it in the card.";
  if (!token) return "Integration token is missing. Set GMAIL_INTEGRATION_TOKEN in Script Properties or enter it in the card.";
  if (!organizationId) return "Organization ID is required.";
  if (!senderEmail) return "Sender email is required.";
  if (!receiverEmail) return "Receiver email is required.";
  if (!body || body.trim().length === 0) return "Body is required.";
  return "";
}

function formatBackendError_(statusCode, data, rawText) {
  const backendMessage = data && data.error ? data.error : rawText;
  if (statusCode === 401) return "Invalid integration token. Backend said: " + backendMessage;
  if (backendMessage && backendMessage.indexOf("NEBIUS") !== -1) return "LLM configuration appears missing or invalid. Backend said: " + backendMessage;
  return "Backend returned HTTP " + statusCode + ": " + backendMessage;
}

function formatScores_(scores) {
  if (!scores) return "No scores returned.";
  return Object.keys(scores).map(function(key) {
    return escapeHtml_(key) + ": " + escapeHtml_(String(scores[key]));
  }).join("<br>");
}

function formatSuggestions_(suggestions) {
  if (!suggestions.length) return "No suggestions returned.";
  return suggestions.slice(0, 5).map(function(item, index) {
    const issue = item.issue || "Suggestion";
    const replacement = item.suggested_replacement || "";
    return (index + 1) + ". <b>" + escapeHtml_(issue) + "</b><br>" + escapeHtml_(replacement);
  }).join("<br><br>");
}

function formatInlineSuggestions_(suggestions) {
  if (!suggestions.length) return "No inline suggestions returned.";
  return suggestions.slice(0, 8).map(function(item, index) {
    return [
      (index + 1) + ". <b>" + escapeHtml_(item.issue || "Suggestion") + "</b>",
      "Target: " + escapeHtml_(item.target_text || ""),
      "Replacement: " + escapeHtml_(item.suggested_replacement || ""),
      item.reason ? "Reason: " + escapeHtml_(item.reason) : "",
    ].filter(Boolean).join("<br>");
  }).join("<br><br>");
}

function latestPreviewSpan_(body) {
  const value = String(body || "").trim();
  if (!value) return "";
  const paragraphs = value.split(/\n\s*\n/).map(function(item) { return item.trim(); }).filter(Boolean);
  return paragraphs.length ? paragraphs[paragraphs.length - 1] : value;
}

function formValue_(inputs, key) {
  const item = inputs[key];
  if (!item || !item.stringInputs || !item.stringInputs.value || !item.stringInputs.value.length) return "";
  return String(item.stringInputs.value[0]).trim();
}

function getProperty_(key, fallback) {
  return PropertiesService.getScriptProperties().getProperty(key) || fallback || "";
}

function saveIfPresent_(key, value) {
  if (value) PropertiesService.getScriptProperties().setProperty(key, value);
}

function trimTrailingSlash_(value) {
  return String(value || "").replace(/\/+$/, "");
}

function parseJson_(value) {
  try {
    return JSON.parse(value);
  } catch (error) {
    return {};
  }
}

function escapeHtml_(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
