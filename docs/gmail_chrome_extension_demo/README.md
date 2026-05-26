# Receiver-Aware Gmail Chrome Extension Demo

This is a local/developer Chrome extension demo for Gmail. It injects a small receiver-aware panel into Gmail compose windows and calls the existing Django Gmail endpoints.

It is intentionally not a Chrome Web Store-ready extension.

## What It Does

- Detects Gmail compose body editors.
- Adds a small Toki launcher button near the compose controls.
- Opens a floating Toki Settings panel next to the compose window.
- Debounces typing and calls:
  ```text
  /api/v1/integrations/gmail/inline-suggestions/preview/
  ```
- Shows numbered inline suggestions.
- Uses the browser Highlight API where available to highlight target text without changing the draft.
- Lets you accept/dismiss suggestions.
- Calls full analysis with:
  ```text
  /api/v1/integrations/gmail/analyze-draft/
  ```

## What It Does Not Do Yet

- It is not production packaged.
- It is not Chrome Web Store reviewed.
- It does not use Google OAuth.
- It does not automatically infer every recipient from Gmail's internal DOM.
- It does not support every Gmail compose/reply/forward layout.
- It does not guarantee compatibility if Gmail changes its DOM.

## Backend Setup

Run Django and expose it with ngrok or Railway:

```bash
python manage.py migrate
python manage.py seed_pseudo_org
python manage.py runserver 127.0.0.1:8000
ngrok http 8000
```

Set:

```text
COMMS_GMAIL_INTEGRATION_TOKEN=your-token
ALLOWED_HOSTS=127.0.0.1,localhost,your-ngrok-host.ngrok-free.dev
```

Verify:

```bash
curl -H "ngrok-skip-browser-warning: true" \
  -H "X-Gmail-Integration-Token: your-token" \
  https://your-ngrok-host.ngrok-free.dev/api/v1/integrations/gmail/health/
```

## Install Locally

1. Open Chrome or Edge.
2. Go to:
   ```text
   chrome://extensions
   ```
   or:
   ```text
   edge://extensions
   ```
3. Enable **Developer mode**.
4. Click **Load unpacked**.
5. Select:
   ```text
   docs/gmail_chrome_extension_demo
   ```
6. Click **Details > Extension options**.
7. Fill:
   ```text
   Backend URL: https://your-ngrok-host.ngrok-free.dev
   Gmail integration token: your-token
   Organization ID: Northstar Labs org id
   Default sender email: rina@northstar.test
   ```
8. Save.
9. Open Gmail and compose a message.

## Northstar Labs Demo Emails

Run:

```bash
python manage.py shell -c "from comms.models import Organization, Employee; org=Organization.objects.get(name='Northstar Labs'); Employee.objects.filter(organization=org, name='Rina Tal').update(email='rina@northstar.test'); Employee.objects.filter(organization=org, name='Dana Weiss').update(email='dana@northstar.test'); print(org.id)"
```

Use:

```text
Sender email: rina@northstar.test
Receiver email: dana@northstar.test
Receiver name: Dana Weiss
```

## Demo Flow

1. Compose a Gmail draft.
2. Click the **Toki** launcher near the compose controls.
3. Fill receiver email/name in the floating Toki Settings panel.
4. Type at least 20 characters in the Gmail body.
5. Pause typing for about one second.
6. Review numbered suggestions.
7. Click **Accept** to insert a replacement into the Gmail draft.
8. Click **Analyze Draft** for scores, improved version, short version, explanation, and dashboard link.

## Notes

The extension uses a content script and service worker. The service worker performs backend fetches with `X-Gmail-Integration-Token`, so Django does not need browser CORS changes for this demo.

The editable settings live in a floating panel outside Gmail's compose DOM. This avoids Gmail stealing focus from sender/receiver email fields while you type.
