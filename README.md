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
COMMS_GMAIL_INTEGRATION_TOKEN=change-me-gmail-demo-token
COMMS_OUTLOOK_INTEGRATION_TOKEN=change-me-outlook-demo-token
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

Gmail compose demo:

```text
http://127.0.0.1:8000/integrations/gmail/demo/
```

Gmail Apps Script / Workspace Add-on demo files:

```text
docs/gmail_apps_script_demo/
```

Gmail Chrome extension demo files:

```text
docs/gmail_chrome_extension_demo/
```

Outlook Office Add-in demo files:

```text
docs/outlook_office_addin_demo/
```

For a Gmail-side demo, seed the Acme demo organization and users:

```bash
python manage.py seed_gmail_demo_org
```

To test the Gmail demo with pseudo users after seeding, give two existing employees emails:

```bash
python manage.py shell -c "from comms.models import Employee; Employee.objects.filter(name='Rina Tal').update(email='rina@example.com'); Employee.objects.filter(name='Dana Weiss').update(email='dana@example.com')"
```

Then open the Gmail demo page and use:

```text
organization: Northstar Labs
sender email: rina@example.com
receiver email: dana@example.com
intent: Request
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
COMMS_GMAIL_INTEGRATION_TOKEN=...
COMMS_OUTLOOK_INTEGRATION_TOKEN=...
COMMS_OUTLOOK_ALLOWED_ORIGINS=*
WEAVE_TRACING=false
WEAVE_PROJECT=your-team/communication-agent
WANDB_API_KEY=...
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

### LangGraph communication agent

`MessageAnalyzer.analyze(...)` now runs through a LangGraph-based workflow while preserving the existing public API. The graph normalizes input, routes simple messages through deterministic fast paths, calls context tools only when needed, reuses the existing LLM analysis for rewrites, validates the final response, and stores runtime metadata on `Message.raw_llm_response["agent_metadata"]`.

Graph docs and the Mermaid source live in:

```text
docs/communication_agent_graph.md
docs/communication_agent_graph.mmd
```

Run the benchmark and aggregate metrics with:

```bash
python manage.py benchmark_communication_agent
python manage.py communication_agent_metrics
```

Optional W&B Weave tracing:

```bash
export WEAVE_TRACING=true
export WEAVE_PROJECT=your-team/communication-agent
export WANDB_API_KEY=...
python manage.py runserver
```

Check configuration and send a small test trace:

```bash
python manage.py weave_status
python manage.py weave_status --send-test-trace
```

When enabled, the graph logs a trace for each agent run and nested traces for each graph node. Gmail/browser inline-preview calls are also traced as `communication_agent.inline_preview`, with nested preview-node traces and an execution summary for nodes, tools, steps, and latency. Trace inputs are sanitized to route, message length, receiver/company, selected tools, and runtime metadata rather than full prompt payloads.

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

The importer and pseudo-org seed data also support richer context:
- `context` for operating context, priorities, customer segments, constraints, and communication patterns
- `projects` for current or planned workstreams with owners, teams, risks, dependencies, and stakeholders
- `meetings` for recurring or recent meeting context, decisions, open questions, and action items

## Scheduled Automations

These commands are intentionally simple Django management commands. They do not require Celery and can later be run by cron, Railway scheduled jobs, GitHub Actions, or n8n.

Generate weekly communication metrics:

```bash
python manage.py weekly_team_communication_report
python manage.py weekly_team_communication_report --organization-id 1
```

Check for org-values drift signals over the last 30 days:

```bash
python manage.py org_values_drift_check
python manage.py org_values_drift_check --organization-id 1
```

Create pending feedback reminders for stale messages without sending email or calling webhooks:

```bash
python manage.py stale_feedback_reminder --days 7
python manage.py stale_feedback_reminder --organization-id 1 --days 7
```

Import employees from CSV and create default receiver prompts for new/onboarded employees:

```bash
python manage.py import_employees_csv employees.csv --organization-id 1
```

CSV columns:

```text
name,email,role,team,manager_email,seniority_level
```

## Webhook / n8n / Zapier Integration

Outgoing webhooks can deliver scheduled automation events to tools like n8n, Zapier, Make, Slack workflows, or internal receivers.

Create a subscription in Django admin:

```text
http://127.0.0.1:8000/admin/comms/webhooksubscription/
```

Set:

```text
organization: target organization
name: n8n reminders
target_url: your n8n/Zapier/Make webhook URL
secret: shared signing secret
event_types: ["feedback.missing", "weekly_report.generated", "org_values_drift.checked"]
is_active: checked
```

Recommended event types:

```text
feedback.missing
weekly_report.generated
org_values_drift.checked
receiver_profile.refresh_proposed later
```

Webhook requests are signed with HMAC SHA256 in:

```text
X-ReceiverAware-Signature
```

The event name is also sent in:

```text
X-ReceiverAware-Event
```

Example n8n flow:
1. Webhook trigger receives the event.
2. Branch by `event_type`.
3. For `feedback.missing`, send an email or Slack reminder.
4. For weekly/drift reports, store the event in Google Sheets or notify an ops channel.

Test by creating a matching subscription, then run:

```bash
python manage.py stale_feedback_reminder --days 7
python manage.py weekly_team_communication_report
python manage.py org_values_drift_check
```

To redeliver one event while debugging:

```bash
python manage.py deliver_webhook_event --event-id 123
```

## Receiver Profile Refresh Approval Flow

Receiver profile refresh is intentionally approval-based. The system creates pending proposals only; it does not automatically overwrite `Employee.receiver_prompt`.

Generate pending proposals from recent receiver feedback and suggestion decisions:

```bash
python manage.py monthly_receiver_profile_refresh
python manage.py monthly_receiver_profile_refresh --organization-id 1
```

Review and approve/reject proposals in Django admin:

```text
http://127.0.0.1:8000/admin/comms/receiverprofilerefreshproposal/
```

The proposal command uses:
- receiver feedback counts and common feedback flags
- accepted inline suggestion counts
- rejected inline suggestion counts

Approval applies only the proposal's safe fields:
- `receiver_prompt_additions`, appended under a marked section
- `communication_preferences_updates`, merged into existing preferences
- `pain_points_updates`, appended without duplicates

Rejection does not change the employee profile.

Webhook event:

```text
receiver_profile.refresh_proposed
```

Use this event in n8n/Zapier to notify an admin or manager that a receiver profile refresh proposal is waiting for review.

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

Gmail integration demo endpoints use `X-Gmail-Integration-Token`:

```bash
curl -H "X-Gmail-Integration-Token: $COMMS_GMAIL_INTEGRATION_TOKEN" \
	http://127.0.0.1:8000/api/v1/integrations/gmail/health/
```

```bash
curl -H "X-Gmail-Integration-Token: $COMMS_GMAIL_INTEGRATION_TOKEN" \
	-H "Content-Type: application/json" \
	-d '{"organization_id":1,"sender_email":"rina@example.com","receiver_email":"dana@example.com","receiver_name":"Dana Weiss","subject":"Status","body":"Fix this today.","intent":"request"}' \
	http://127.0.0.1:8000/api/v1/integrations/gmail/analyze-draft/
```

Outlook Office Add-in demo endpoints use `X-Outlook-Integration-Token`:

```bash
curl -H "X-Outlook-Integration-Token: $COMMS_OUTLOOK_INTEGRATION_TOKEN" \
	http://127.0.0.1:8000/api/v1/integrations/outlook/health/
```

```bash
curl -H "X-Outlook-Integration-Token: $COMMS_OUTLOOK_INTEGRATION_TOKEN" \
	-H "Content-Type: application/json" \
	-d '{"organization_id":1,"sender_email":"rina@example.com","receiver_email":"dana@example.com","receiver_name":"Dana Weiss","subject":"Status","body":"Fix this today.","intent":"request","channel":"outlook"}' \
	http://127.0.0.1:8000/api/v1/integrations/outlook/analyze-draft/
```

```bash
curl -H "X-Outlook-Integration-Token: $COMMS_OUTLOOK_INTEGRATION_TOKEN" \
	-H "Content-Type: application/json" \
	-d '{"organization_id":1,"sender_email":"rina@example.com","receiver_email":"dana@example.com","receiver_name":"Dana Weiss","intent":"request","full_draft":"Fix this today.","changed_text":"Fix this today.","surrounding_context":"Subject: Status"}' \
	http://127.0.0.1:8000/api/v1/integrations/outlook/inline-suggestions/preview/
```

## Integration stubs

The following are intentionally light adapter stubs:

```text
comms/integrations/base.py
comms/integrations/slack_adapter.py
comms/integrations/teams_adapter.py
comms/integrations/gmail_adapter.py
comms/integrations/outlook_adapter.py
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
