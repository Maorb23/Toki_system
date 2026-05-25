# Gmail Apps Script Demo

## What This Is

This is a test Gmail / Google Workspace Add-on wrapper that calls the Django receiver-aware Gmail analysis endpoint:

```text
POST /api/v1/integrations/gmail/analyze-draft/
```

It uses Apps Script `CardService` and `UrlFetchApp`, so it can run inside Gmail as an unpublished test deployment.

## What This Is Not

- Not Marketplace-ready.
- Not production Google OAuth.
- Not domain-wide Workspace deployment.
- Not automatic live typing inside Gmail.
- Not automatically rewriting the Gmail compose window.
- Not a browser extension.

## Files

```text
Code.gs
appsscript.json
README.md
```

Copy `Code.gs` and `appsscript.json` into a Google Apps Script project.

## Configuration

`Code.gs` includes placeholder constants:

```javascript
const DEFAULT_BACKEND_URL = "https://YOUR-DOMAIN.com";
const DEFAULT_ORGANIZATION_ID = "1";
```

Do not commit real secrets. Set runtime config in Apps Script:

1. Open the Apps Script project.
2. Go to **Project Settings**.
3. Add script properties:

```text
BACKEND_URL=https://your-public-django-url
GMAIL_INTEGRATION_TOKEN=your-local-or-railway-token
ORGANIZATION_ID=1
```

You can also enter these values directly in the add-on card while testing. The card saves non-empty config values back to Script Properties for convenience.

## Django Setup

Apps Script calls Django server-side with `UrlFetchApp`, so browser CORS is not required.

Set Django environment variables:

```bash
COMMS_GMAIL_INTEGRATION_TOKEN=your-token
COMMS_API_KEY=change-me
NEBIUS_API_KEY=...
NEBIUS_MODEL=...
NEBIUS_BASE_URL=https://api.studio.nebius.com/v1
```

Run:

```bash
python manage.py migrate
python manage.py seed_gmail_demo_org
python manage.py runserver
```

For local Gmail testing, expose Django publicly with ngrok or deploy to Railway:

```bash
ngrok http 8000
```

Use the public HTTPS URL as `BACKEND_URL`.

Verify health:

```bash
curl -H "X-Gmail-Integration-Token: your-token" \
  https://your-public-django-url/api/v1/integrations/gmail/health/
```

## Demo Users

The optional Django command creates:

```text
Organization: Acme Demo Org
Sender: Gmail Demo Sender <sender@acme.test>
Receiver: Dana Receiver <receiver@acme.test>
Org values: Clarity, Ownership, Respectful urgency
```

Use the printed organization ID as `ORGANIZATION_ID`.

## How To Test As An Unpublished Gmail Add-On

1. Deploy Django locally with ngrok or deploy it to Railway.
2. Set Django environment variables:
   - `COMMS_GMAIL_INTEGRATION_TOKEN`
   - `COMMS_API_KEY` if needed elsewhere
   - `NEBIUS_API_KEY`
   - `NEBIUS_MODEL`
3. Run:
   ```bash
   python manage.py migrate
   python manage.py seed_gmail_demo_org
   python manage.py runserver
   ```
4. Verify:
   ```text
   GET /api/v1/integrations/gmail/health/
   ```
5. Use the pseudo users:
   ```text
   sender@acme.test
   receiver@acme.test
   ```
6. Open Google Apps Script.
7. Create a new Apps Script project.
8. Copy `Code.gs` and `appsscript.json` into the project.
9. Set script properties:
   - `BACKEND_URL`
   - `GMAIL_INTEGRATION_TOKEN`
   - `ORGANIZATION_ID`
10. Use **Deploy > Test deployments > Install**.
11. Refresh Gmail.
12. Open the add-on inside Gmail.
13. Enter draft details and click **Analyze with Receiver-Aware**.

Use **Preview inline suggestions** to call the lightweight inline preview endpoint and show target/replacement suggestions in the add-on card.

Google Workspace add-ons can be installed for unpublished testing through Apps Script test deployments, which is enough for this demo without Marketplace publishing.

## Manual Test Checklist

- Add-on opens in Gmail.
- Missing backend URL shows a clear error.
- Missing token shows a clear error.
- Missing receiver email shows a clear error.
- Empty body shows a clear error.
- Invalid token returns a clear backend error.
- Missing Nebius config returns a clear backend error.
- Valid draft returns scores, suggestions, improved version, short version, explanation, and dashboard link.
- Inline preview returns target/replacement suggestions when the draft has a clear editable span.

## Inline Preview Limitation

The Django demo page can draw highlights and chips directly over its own textarea. Gmail Apps Script `CardService` cannot draw overlays inside Gmail's compose box or react to each keystroke. This demo therefore provides a manual **Preview inline suggestions** button. It calls the same Django `InlineSuggestionPreviewer`, then displays target text and suggested replacements in the add-on card for manual copy/edit.

## Payload Sent To Django

```json
{
  "organization_id": "1",
  "sender_email": "sender@acme.test",
  "receiver_email": "receiver@acme.test",
  "receiver_name": "Dana Receiver",
  "subject": "Project update",
  "body": "Can you fix this today?",
  "intent": "request"
}
```
