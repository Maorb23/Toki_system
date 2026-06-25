---
name: outlook-office-addin-debugging
description: Debug and fix local Outlook Office add-in taskpane demos, especially Office.js compose-mode add-ins served from localhost/127.0.0.1 with Django/ngrok backends. Use when Outlook add-ins fail to start, taskpane assets do not load, inline suggestions break after apply, live preview sends partial text, Office.js body text differs after setAsync/getAsync, or local HTTPS/manifest/ngrok/caching issues appear.
---

# Outlook Office Add-in Debugging

## Core Lessons

Outlook Office add-ins are harder to debug than web or Gmail demos because the compose body is not a normal editable DOM. The taskpane reads and writes through async Office.js APIs:

- `item.body.getAsync(Office.CoercionType.Text)`
- `item.body.setAsync(text, { coercionType: Office.CoercionType.Text })`

Never assume the text returned after `setAsync` is byte-for-byte identical to the text written. Outlook may normalize line endings, whitespace, signatures, quoted content, or formatting-derived text.

## Local Serving Checklist

Verify the exact manifest URL, not a nearby URL.

Prefer `https://127.0.0.1:3000/taskpane.html` over `https://localhost:3000/taskpane.html` if `localhost` routes to another local service or IPv6 listener.

Check what is actually served:

```powershell
node -e "const https=require('https'); https.get('https://127.0.0.1:3000/taskpane.js',{rejectUnauthorized:false},res=>{let b='';res.on('data',d=>b+=d);res.on('end',()=>console.log({statusCode:res.statusCode,cacheControl:res.headers['cache-control'],length:b.length}));})"
```

Run the static server with cache disabled during debugging:

```bash
npx http-server docs/outlook_office_addin_demo -S \
  -C 'C:\Users\maorb\.office-addin-dev-certs\localhost.crt' \
  -K 'C:\Users\maorb\.office-addin-dev-certs\localhost.key' \
  -p 3000 \
  -c-1
```

If manifest URLs changed, remove and re-add the sideloaded add-in. If only JS/CSS changed, usually close and reopen the taskpane or use Ctrl+F5.

## Backend And Ngrok

Separate taskpane startup from backend API failures.

- If ngrok connections stay at `0`, Outlook probably never loaded the taskpane.
- If the taskpane loads but API calls fail, check token, CORS, CSRF, allowed hosts, and ngrok reachability.
- Use `ngrok-skip-browser-warning: true` in taskpane fetches.
- Test ngrok without proxy when results are inconsistent:

```powershell
curl.exe --noproxy "*" -H "ngrok-skip-browser-warning: true" https://YOUR-NGROK/api/v1/integrations/outlook/health/
```

## Inline Suggestion Apply Rules

Do not apply Outlook inline suggestions by blindly searching `target_text` later. The body may have changed since preview.

Preferred behavior:

- Store a local range for each suggestion when preview results arrive.
- On apply, verify `body.slice(range.start, range.end) === target_text`.
- If the range no longer matches, re-anchor near the expected range using exact, case-insensitive, or whitespace-tolerant matching.
- Apply by range, not by first global `indexOf`.
- After apply, read the body back from Outlook with `getAsync`.
- Use the post-apply body, not the written body, to update remaining suggestion ranges.
- Remove overlapping suggestions after one suggestion is accepted.
- Shift and re-anchor non-overlapping later suggestions.
- Discard stale preview responses if they were generated before a body mutation.

## Live Preview Windowing

Outlook live preview should not send raw suffixes such as:

```text
ake it great?
Mo
```

Compute a preview window instead:

- Diff previous body vs current body.
- Expand the changed range to sentence or phrase boundaries.
- Include surrounding context separately.
- Do not preview very short trailing partial words.
- For manual preview, consider sending the full draft.

Good changed-text windows look like:

```text
How to make it great?
Moreover your wife is hot, when can I see her again?
```

Bad changed-text windows look like:

```text
ake it great?
reover how are you today?
```

## Deterministic Safety Rules

Do not rely only on the LLM to catch obvious inappropriate personal or sexual comments. Add shared deterministic backend rules for all integrations when the content is clearly unacceptable.

Examples to catch:

```text
your wife is hot, when can I see her again?
your husband is hot when can I see him again?
your partner is hot
```

Example replacement:

```text
When are you available to discuss the next steps?
```

Put these rules in shared inline-preview backend logic, not only in Outlook JS, so Gmail and workspace demos benefit too.

## Known Failure Patterns

Line-ending normalization:

```text
updated_body_length: 151
post_apply_body_length: 150
range_slice: "ow to make it good?"
range_matches_target: false
```

This usually means Outlook normalized `\r\n` to `\n`, shifting later ranges by one character. Fix by reading the actual post-apply body and re-anchoring remaining suggestions.

Stale preview response:

```text
preview_response body_length: 129
current_body_length: 150
```

This means an old live-preview response arrived late and may overwrite visible suggestions with stale ranges. Track preview request IDs and body mutation versions, then discard responses generated before a body mutation or superseded by a newer request.

Partial changed text:

```text
target_text: "ake it great?"
post_apply_body_preview: "How to mWhat specific improvements..."
```

This means live preview sent a chopped suffix instead of a complete phrase. Fix changed-text windowing before changing apply logic.

## Instrument Before Fixing

Add an in-taskpane debug panel before changing apply logic blindly.

Log current suggestion state:

```text
local_id
backend_id
target_text
replacement
range start/end
body.slice(range.start, range.end)
range_matches_target
nearest_target_index
target_occurrence_count
current_body_length
last_previewed_body_length
```

Log events:

```text
preview_response
preview_discarded
preview_error
apply_attempt
apply_success
apply_error
dismiss
```

Use the debug output to determine whether the failure is stale preview data, line-ending normalization, repeated target text, bad backend target text, wrong range shifting, or bad live-preview changed text.

## Minimal Fix Strategy

When fixing Outlook apply bugs, keep changes narrow:

- Do not change the backend previewer unless debug proves backend targets are wrong or a deterministic rule is required.
- Do not change the manifest unless the taskpane URL is wrong.
- Do not change polling unless stale previews are proven.
- Do not auto-refresh suggestions after apply unless explicitly desired.
- Prefer range verification, post-apply body readback, stale-response guards, complete changed-text windows, and debug instrumentation.

## Validation Snippets

Validate served frontend assets:

```powershell
node -e "const https=require('https'); https.get('https://127.0.0.1:3000/taskpane.js',{rejectUnauthorized:false},res=>{let b='';res.on('data',d=>b+=d);res.on('end',()=>console.log({statusCode:res.statusCode,cacheControl:res.headers['cache-control'],hasDebug:b.includes('addDebugEvent'),length:b.length}));})"
```

Validate JavaScript parsing without relying on `node --check` path behavior:

```powershell
Get-Content docs\outlook_office_addin_demo\taskpane.js -Raw | node -e "let s=''; process.stdin.on('data', d => s += d); process.stdin.on('end', () => { new Function(s); console.log('taskpane.js parsed'); });"
```

For backend Python changes, restart or rely on Django `runserver` autoreload, then test the relevant Django endpoint or focused unit tests from the active Python/WSL environment.
