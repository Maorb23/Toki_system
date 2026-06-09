# Receiver-Aware Outlook Office Add-in Demo

This folder contains a minimal Outlook compose-mode Office.js add-in for the Toki receiver-aware communication system.

It reads the current Outlook draft, calls the existing Django communication-analysis flow through an Outlook-specific integration endpoint, shows the analysis result, and lets the user explicitly apply the improved version back into the draft body.

The add-in never sends email.

## Files

```text
manifest.xml
taskpane.html
taskpane.css
taskpane.js
assets/
```

## Backend Endpoint

The task pane calls:

```text
POST /api/v1/integrations/outlook/analyze-draft/
```

For inline suggestion preview, it calls:

```text
POST /api/v1/integrations/outlook/inline-suggestions/preview/
```

Header:

```text
X-Outlook-Integration-Token: your-token
```

Request shape:

```json
{
  "organization_id": "1",
  "sender_email": "sender@company.test",
  "receiver_email": "receiver@company.test",
  "receiver_name": "Dana Receiver",
  "subject": "Subject text",
  "body": "Draft body text",
  "intent": "request",
  "channel": "outlook"
}
```

The response follows the existing draft-analysis demo shape:

```json
{
  "scores": {},
  "suggestions": [],
  "improved_version": "...",
  "short_version": "...",
  "explanation": "...",
  "dashboard_url": "...",
  "dashboard_absolute_url": "...",
  "metadata": {
    "channel": "outlook"
  }
}
```

The task pane also calls this optional event endpoint after the user applies the improved version:

```text
POST /api/v1/integrations/outlook/events/
```

## Backend Setup

Set these variables:

```bash
COMMS_OUTLOOK_INTEGRATION_TOKEN=your-token
COMMS_OUTLOOK_ALLOWED_ORIGINS=*
ALLOWED_HOSTS=127.0.0.1,localhost,your-ngrok-host.ngrok-free.dev
```

Run Django:

```bash
python manage.py migrate
python manage.py seed_pseudo_org
python manage.py runserver 127.0.0.1:8000
```

For Outlook Web or desktop task panes, use an HTTPS backend URL such as ngrok or Railway:

```bash
ngrok http 8000
```

Verify:

```bash
curl -H "X-Outlook-Integration-Token: your-token" \
  https://your-ngrok-host.ngrok-free.dev/api/v1/integrations/outlook/health/
```

## Host The Task Pane

Office add-ins require HTTPS task pane URLs. Serve this folder from `https://localhost:3000` or update every `https://localhost:3000` URL in `manifest.xml` to your own HTTPS host.

One simple local option is any HTTPS static server that serves:

```text
docs/outlook_office_addin_demo/
```

The manifest currently expects:

```text
https://localhost:3000/taskpane.html
```

## Sideload In Outlook

1. Serve the task pane over HTTPS.
2. Open Outlook and start composing an email.
3. Open add-ins management.
4. Choose add a custom add-in from file.
5. Select `manifest.xml`.
6. In the compose window, choose `Open Toki`.

Exact menu names vary between Outlook desktop, new Outlook, and Outlook on the web.

## Demo Flow

1. Open Outlook compose.
2. Open the Toki task pane.
3. Enter backend URL, token, organization ID, and sender if not inferred.
4. Write a draft and add a To recipient.
5. Click `Analyze Draft`.
6. Review scores, suggestions, improved version, short version, and explanation.
7. Click `Apply Improved Version` only if you want to replace the draft body.

For lightweight review before full analysis:

1. Click `Preview Inline Suggestions`.
2. Review suggestion cards.
3. Click `Apply` on a suggestion to replace the first matching target text in the Outlook draft body.
4. Click `Dismiss` to hide a suggestion and log the rejection event.

## What It Does

- Reads compose subject, body, and To recipients through Office.js.
- Uses `Office.context.mailbox.userProfile.emailAddress` as the sender when available.
- Calls the Outlook integration endpoint with a simple integration token.
- Shows backend scores, suggestions, improved version, short version, explanation, and dashboard link.
- Previews inline suggestions as task pane cards.
- Lets the user explicitly apply or dismiss each preview suggestion.
- Replaces the draft body only after the user clicks `Apply Improved Version`.
- Logs an Outlook apply event when possible.

## What It Does Not Do Yet

- It does not use Microsoft Graph.
- It does not use Microsoft SSO.
- It does not publish to AppSource.
- It does not perform enterprise admin deployment.
- It does not request calendar or mailbox-wide permissions.
- It does not automatically send email.
- It does not update the Outlook subject line.
- It does not implement Gmail-style in-body highlighting.

## Known Limitations

- Demo only.
- Compose-mode only.
- Production OAuth is not implemented.
- The task pane and backend usually both need HTTPS in Outlook.
- Unknown receivers are created as demo receiver profiles, matching the demo integration behavior.
- Inline suggestions are displayed as review cards, not in-place highlights.
- Applying a preview suggestion replaces the first exact text match in the plain-text draft body.
