# v2.4 build — shared contracts for the four builders

Read `docs/V24_SPEC.md` (the product spec) and this file before writing code. The foundation is already committed
(`git log`): feature keys and limits in `backend/app/plans.py`, the setup in `backend/app/v24.py`, shared helpers in
`backend/app/growth_core.py`, router stubs in `backend/app/routers/{prospects,leadbrief,dealfill,callcoach,aiteam}.py`,
page stubs `frontend/src/pages/{Prospects,CallCoach,AiTeam}.jsx` (routes already in `App.jsx`), test helpers in
`backend/tests/v24_helpers.py`, and the outreach rule in `backend/app/agents/approvals.py` (`OWN_ONLY`).

## Rules (from Harsh — none of these may be broken)

- No fake activity, fake numbers, fake receipts, or "done" claims that are not proven. Every number on a screen is counted
  from saved records.
- Never put real customer messages, names, phone numbers or emails into code, fixtures or screenshots. Invented data only.
- Never change, weaken or delete existing tests or safety checks. Add tests for everything you build.
- No secrets in frontend code. Never ask for passwords or API keys.
- Do not deploy. Do not run `git commit`, `git reset`, `git checkout` or `git stash` — the coordinator commits.
- When unsure, pick the option that is safer for the business owner and their customers, write the reason in your final
  report, and keep going.
- Owners see "AI tokens" (never "runs") and category names (never a software company's name, e.g. "WhatsApp", not "AiSensy";
  "Google Maps" is fine where Google's attribution requires it).
- Plain language for a 50-year-old Indian business owner. Short sentences. No hype words.

## Environment

- Repo: `/home/claude/action-hub`. Backend FastAPI + Motor (MongoDB 3.6 on 127.0.0.1:27017). If it is down, run
  `bash /tmp/claude-0/-home-claude/1f5cf576-3d3b-55b4-b5d3-fbe0945ab906/scratchpad/mongoup.sh`.
- Run only your own tests plus any existing test files you touched:
  `cd backend && timeout 900 python3 -m pytest -q -p no:cacheprovider tests/test_v24_<yours>.py`
  Do NOT run the full suite (the coordinator does; two CPUs are shared by four builders). Each pytest process uses its own
  throwaway database. `AI_PROVIDER=mock` in tests: every AI job needs a mock (see below).
- Check the frontend compiles WITHOUT touching the shared build folder:
  `cd frontend && npx vite build --outDir /tmp/claude-0/-home-claude/1f5cf576-3d3b-55b4-b5d3-fbe0945ab906/scratchpad/build_<you> --emptyOutDir`
- Python is `python3`; `pip install --break-system-packages` only if truly needed (prefer not: the developer's
  requirements.txt must not grow without reason; if you add a package, say so in your report).

## Patterns to copy (read these files first)

- Feature gate on an endpoint: `ctx: Ctx = Depends(feature("prospects", "manager"))` (`backend/app/context.py`).
- One AI job = one AI token: `await run_ai(ctx.owner, kind, job_coroutine, title, save_output=False, check=shape_fn)`
  (`backend/app/services.py`). It refunds the token if the AI fails or `check` raises. AI jobs live in `backend/app/ai/*.py`
  and call `complete(system, prompt, mock_fn, max_tokens)` (`backend/app/ai/provider.py`); `mock_fn()` returns the dict used
  in tests. Use `RULES` and `profile_text(p)` from `backend/app/ai/tasks.py` (never invent prices, names, facts). Every AI
  job also has a rules version (no AI) for tests and for when the AI's answer can't be used — see `backend/app/ai/crm_tasks.py`.
- Business profile: `from .routers.hub import profile_of` → `await profile_of(ws)`.
- Limits: `await growth_core.take(owner, "<limit key>")` before the work, `await growth_core.give_back(owner, key)` if it
  then fails; `await growth_core.left(owner, key)` for the page. Limit keys: `prospect_searches`, `prospects_kept`,
  `outreach_per_day`, `briefs`, `calls_scored`.
- AI roles: wrap work in `async with growth_core.working(ws, role):`, log every finished action with
  `await growth_core.log_action(ws, role, "plain one-line text", ref="<kind>:<id>")`, and before AUTOMATIC work (not a
  button the owner pressed) check `await growth_core.role_on(ws, role)`.
- Background work after a request (so a customer's form never waits): copy `inquiries.after_lead` / `settle()`
  (`backend/app/routers/inquiries.py`). Tests call your `settle()`.
- Scheduled agents: `agents/catalog.register(key, {...}, runner)` — see `agents/catalog.py` docstring and
  `routers/inquiries.py` / `routers/winback.py` for examples. Make sure the module that registers is imported at start-up
  (your router module imports it).
- Reading a website safely: `backend/app/fetch.py` (`read_site`, `fetch_text`). Tests use a fake site (look at how
  `tests/test_competitors.py` fakes pages / Places with `httpx.MockTransport` and `settings.fetch_private_ok`).
- Google Places: `backend/app/competitor_watch.py` (`places_search`, `PLACES_TRANSPORT`, `places_ready`). Key:
  `settings.google_places_api_key`. Tests swap `PLACES_TRANSPORT` for an `httpx.MockTransport`.
- Frontend: `Sheet`, `useHub`, `Busy`, `Field`, `FormError`, `Modal` in `frontend/src/ui.jsx`; `Loading`, `Tabs` in
  `frontend/src/v2.jsx`; `api(path, {method, body})` in `frontend/src/api.js`. One CSS file per page, imported by the page.
  Look at `pages/Pipeline.jsx` + `pipeline.css` and `pages/Contacts.jsx` for the current look (dark, gold/teal). Phone
  width 390 px must not scroll sideways.

## Who owns which files (do not edit another builder's files)

| Builder | Owns (create or edit) |
|---|---|
| A — Prospecting | `app/prospecting.py`, `app/routers/prospects.py`, `app/ai/prospect_tasks.py`, `frontend/src/pages/Prospects.jsx`, `frontend/src/pages/prospects.css`, `tests/test_v24_prospects.py`; small edits to `app/agents/approvals.py` (only `decide` for own-link phone lookup and `after_sent` hook) and `frontend/src/pages/Approvals.jsx` (outreach messages show only "Send on WhatsApp") |
| B — Lead research + Pipeline fills itself | `app/lead_research.py`, `app/deal_fill.py`, `app/routers/leadbrief.py`, `app/routers/dealfill.py`, `app/ai/brief_tasks.py`, `app/ai/dealfill_tasks.py`, `tests/test_v24_lead_research.py`, `tests/test_v24_deal_fill.py`; edits to `app/agents/hooks.py` (call lead research on a new lead), `app/inbound.py` (email from a known contact), `app/routers/crm.py` (only if the deal view must carry the brief / pending updates), `frontend/src/pages/Pipeline.jsx`, `pipeline.css`, `frontend/src/pages/Inquiries.jsx`, `inquiries.css` |
| C — Call coach | `app/call_coach.py`, `app/routers/callcoach.py`, `app/ai/callcoach_tasks.py`, `frontend/src/pages/CallCoach.jsx`, `frontend/src/pages/callcoach.css`, `tests/test_v24_call_coach.py` |
| D — AI team, Weekly growth line, Quick answers | `app/ai_team.py`, `app/routers/aiteam.py`, `frontend/src/pages/AiTeam.jsx`, `frontend/src/pages/aiteam.css`, `tests/test_v24_ai_team.py`; edits to `app/dashboard_widgets.py` and `frontend/src/pages/Dashboard.jsx` (+ `dashboard.css`) for the Quick answers tile |

Shared files nobody edits (tell the coordinator in your report if you need a change): `plans.py`, `v24.py`,
`growth_core.py`, `main.py`, `App.jsx`, `ui.jsx`, `api.js`, `services.py`, `context.py`.

## The records (what each builder writes and others read)

All documents carry `ws` (the owner's id = workspace) and are per business. Dates are UTC datetimes unless noted.

**prospect_searches** (A writes) `{_id, ws, kinds: [str], area: str, radius_km: int|None, found: int, new: int, at, by}`
— `found` = results returned, `new` = shown after filtering out closed businesses and existing contacts.

**prospects** (A writes) `{_id, ws, source: "google"|"list", place_id: str|None, kind: str,`
`name, phone, email, website, city: (only for source "list" — the owner's own data; NEVER stored for source "google"),`
`status: "kept"|"drafted"|"contacted"|"replied"|"deal"|"not_interested"|"dnc",`
`notes: str, kept_at, drafted_at, contacted_at, replied_at, closed_at, lead_id, followups_sent: int, next_followup_on: "YYYY-MM-DD"|None, updated_at}`

**prospect_block** (A writes) `{_id, ws, place_id?, phone?, at}` — "Do not contact", permanent.

**approvals** with `kind: "outreach"`, `agent: "outreach_writer"`, plus `prospect_id` and `step: "first"|"day3"|"day7"`.
"Messages you sent" = `{kind: "outreach", status: "sent", via: "own"}` by `decided_at`.

**leads.brief** (B writes, on the lead document) `{status: "done"|"empty"|"failed", who: str, asking: str,`
`questions: [str], first_line: str, sources: [{label, url}], looked_up: bool, at, by: "auto"|<user id>}`.
"Leads researched" = leads with `brief.status == "done"` by `brief.at`.

**deal_updates** (B writes) `{_id, ws, lead_id|None, contact_id|None, source: "paste"|"upload"|"voice"|"call"|"email"|"whatsapp",`
`call_id|None, summary, needs, next_step, follow_up_on: "YYYY-MM-DD"|None,`
`applied: {note: bool, follow_up_on: "YYYY-MM-DD"|None, ...}, suggested: {pstage?, value?, status?, lost_reason?, new_deal?},`
`state: "pending"|"applied"|"dismissed"|"done", undo: {...}, undone_at|None, at, applied_at|None, by}`.
`state` is "pending" while suggestions wait for a tap, "done" when there was nothing to suggest.
"Conversations filed" = deal_updates by `at`; "Updates applied" = `state == "applied"` by `applied_at`.

**gym_sessions** with `kind: "call"`, `source: "call_coach"`, `member: str`, `score: {scores, overall (0–10), fixes, best}`,
`scored_at` (C writes; existing collection — keep its other fields as `routers/growth.py score_call` writes them).
"Calls scored" = those by `scored_at`.

**leads** created from a prospect: `source: "prospecting"`, `prospect_id`. "Deals from new customers" = such leads by
`created_at`; their value = sum of `value` the owner entered.

## AI roles (growth_core.ROLES) and activity refs

| Role key | Logged by | ref |
|---|---|---|
| `prospector` | A (a search, a kept list) | `search:<id>` or `prospect:<id>` |
| `outreach_writer` | A (drafts written, follow-ups) | `prospect:<id>` |
| `researcher` | B (a brief written) | `lead:<id>` |
| `pipeline_keeper` | B (a conversation filed / update applied) | `lead:<id>` (or `update:<id>` with no deal) |
| `call_coach` | C (a call scored) | `call:<session id>` |
| `followup`, `payment_reminder` | already logged by the existing agents | as they are |

Links on the AI team page: `prospect:<id>` → `/prospects/<id>`; `lead:<id>` → `/pipeline?deal=<id>`; `call:<id>` →
`/call-coach?call=<id>`; `search:<id>` → `/prospects`; `update:<id>` → `/pipeline`.

## Endpoint contracts between builders

- **B provides, C calls**: `POST /api/crm/deals/{lead_id}/conversation` JSON `{text: str, source: "call", call_id: str}`
  → `{update: <deal_update view>}`. Same endpoint with `source: "paste"` for pasted chats. Also
  `POST /api/crm/deals/{lead_id}/conversation/upload` (multipart `file`: WhatsApp export .txt/.zip or a voice note) and
  `POST /api/crm/conversation` (no deal yet → a "new deal" suggestion). Feature: `deal_autofill`.
- **A provides**: prospects that reply and become deals create a lead with `source: "prospecting"` through the normal lead
  path (`routers/public.save_lead`-like or `crm` helpers) so the brief and pipeline features see them too.
- **D reads only** the records above, and `approvals`, `dues`, `agent_log`, `growth_working`. D writes nothing for the other
  builders.

## Report when you finish

Short and plain: files created/changed; endpoints; what was tested (test names, pass count); anything in the spec you did
not do or did differently and why; anything you need from the coordinator (a shared-file change). Do not paste code.
