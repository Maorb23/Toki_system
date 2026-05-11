# Receiver-Aware Communication POC

A Django POC for an organizational communication platform.

The product helps a sender improve a draft message for a specific receiver, using:
- receiver communication preferences
- team context
- organization values
- channel context
- LLM-based inline suggestions from Nebius
- receiver feedback after the message is received

This project intentionally has **no fake LLM fallback**.  
If Nebius is not configured, message analysis fails clearly while the rest of the app still works.

## Stack

- Django
- SQLite locally
- PostgreSQL via `DATABASE_URL`
- Gunicorn
- WhiteNoise
- Django templates + vanilla JavaScript
- Nebius OpenAI-compatible chat completions API

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and add:

```bash
NEBIUS_API_KEY=...
NEBIUS_BASE_URL=https://api.studio.nebius.com/v1
NEBIUS_MODEL=...
COMMS_API_KEY=change-me
```

Run:

```bash
python manage.py migrate
python manage.py seed_pseudo_org
python manage.py import_org org_config.sample.json
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## Railway deployment

Set these Railway variables:

```bash
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=False
ALLOWED_HOSTS=your-app.up.railway.app
CSRF_TRUSTED_ORIGINS=https://your-app.up.railway.app
DATABASE_URL=...
NEBIUS_API_KEY=...
NEBIUS_BASE_URL=https://api.studio.nebius.com/v1
NEBIUS_MODEL=...
COMMS_API_KEY=...
```

Railway will use the `Procfile`:

```text
web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn receiver_comm.wsgi:application --log-file -
```

After first deploy, seed the pseudo organization from the Railway shell:

```bash
python manage.py seed_pseudo_org
```

## Main flows

### 1. Dashboard

Shows org/team/employee/message activity.

### 2. Organization graph

Shows teams, employees, and reporting lines. Click an employee to open their profile.

### 3. Employee profile

Shows communication preferences, receiver prompt, and feedback history. Preferences can be edited.

### 4. Message workspace

The sender selects sender, receiver, channel, intent, and enters a message.

The system calls Nebius and expects structured JSON with:
- inline suggestions
- affected text spans
- replacement text
- explanation
- score impacts
- risks
- estimated scores

### 5. Inline suggestions

Suggestions appear above or near relevant text. The sender can accept/reject each suggestion. Scores update deterministically based on accepted suggestion deltas.

### 6. Receiver feedback

After marking a message as sent, the receiver gives feedback. Receiver feedback updates the receiver communication prompt/preferences.

## Organization import

Import a new organization from a JSON config:

```bash
python manage.py import_org path/to/org_config.json
```

See the sample format in `org_config.sample.json`.

## REST API (v1)

All API endpoints require `X-API-Key` and an `X-Org-Id` header (or `org_id` query/body).

Examples:

```bash
curl -H "X-API-Key: $COMMS_API_KEY" http://127.0.0.1:8000/api/v1/orgs/
```

```bash
curl -H "X-API-Key: $COMMS_API_KEY" -H "X-Org-Id: 1" \
	http://127.0.0.1:8000/api/v1/orgs/1/employees/
```

```bash
curl -H "X-API-Key: $COMMS_API_KEY" -H "X-Org-Id: 1" \
	-H "Content-Type: application/json" \
	-d '{"org_id":1,"sender_id":1,"receiver_id":2,"channel":"slack","intent":"request","original_message":"Fix this."}' \
	http://127.0.0.1:8000/api/v1/messages/analyze/
```

## Integration stubs

The following are intentionally light adapter stubs:

```text
comms/integrations/base.py
comms/integrations/slack_adapter.py
comms/integrations/teams_adapter.py
comms/integrations/gmail_adapter.py
```

They define the future boundary:
1. receive draft
2. identify sender/receiver
3. call message improvement service
4. return inline suggestions
5. collect receiver feedback
6. never auto-send

## Important principle

Pseudo organization data is acceptable.  
Fake LLM analysis is not.
