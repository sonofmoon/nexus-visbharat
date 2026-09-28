# Telegram processing and delivery

This upgrade shortens the citizen flow and moves audio/AI processing off the webhook. It is implementation evidence, not a claim of production certification, guaranteed latency or government deployment.

## Citizen journey

1. On the first request, select a language and send text or a voice note. Later requests remember the chosen language; `/language` changes it.
2. Confirm a suggested location, or choose a state and district. Suggestions use explicit recognised district names or the previous district and are never silently accepted. Multiple recognised districts require manual selection.
3. Review the actual text, location, privacy notice and consent together. Ward/area is optional and can be added from review. Select **I Consent & Submit**.
4. Receive a durable reference marked **processing pending**. The worker completes classification/intake and sends the saved-ticket notification using the same reference. `/status <reference>` distinguishes pending, failed and saved requests.

Failed speech recognition keeps the draft at the input step. A placeholder is never presented as recognised speech. The next configured speech provider may be tried; simulated transcripts cannot complete a Telegram voice request. Editing or replacing a draft requires fresh review and consent.

Existing 13-language prompts remain available. New processing/location messages are provided in Tamil, Telugu, Kannada, Hindi and English; other languages currently use an English fallback for these new messages. These translations require fluent-speaker review. District-name recognition is deliberately conservative, especially for inflected native-language names.

## Required processes

Run the web service and these two supervised, continuously running processes against **the same durable PostgreSQL database and application revision**:

```sh
python telegram_outbox_worker.py --role jobs
python telegram_outbox_worker.py --role outbox
```

The first processes queued audio and intake jobs. The second delivers acknowledgements and replies independently, so slow AI work does not block other citizens' messages. The polling interval defaults to one second via `TELEGRAM_OUTBOX_INTERVAL_SECONDS`; this is not a delivery-time guarantee. Both processes need appropriate application configuration. The processing worker needs the configured Google providers; delivery needs the Telegram bot token. Protect credentials with the deployment's secret manager.

**Do not deploy only the web change.** Webhooks no longer flush messages or run AI. Without these workers, requests and messages remain queued. The current Dockerfile starts only the web service: explicitly provision and supervise the workers, with CPU available while idle. A request-scoped background thread or an infrequent scheduled job is not the intended operating mode.

For a local rehearsal only, one combined worker is available:

```sh
python telegram_outbox_worker.py --role all
```

`--once` performs one bounded iteration. Combined mode can delay delivery while a provider call runs; use separate roles for the deployed demonstration. The legacy poller is still a local development tool, not the production worker arrangement.

## Migration and release order

1. Back up the existing database. Run the application's migration procedure once using a migration identity; `telegram_gateway.migrate` creates `telegram_jobs`, `telegram_chat_guards`, `telegram_worker_heartbeats` and adds the outbox lease-owner column. Existing drafts and tickets are retained. The migration should complete before new workers or webhook traffic use these tables.
2. Stop/drain older Telegram delivery and processing instances before switching. Older instances do not implement the new job/lease behaviour and must not share processing traffic during the cutover.
3. Start both new worker roles, then the web revision. Keep schema migration and demo seeding disabled in serving processes after migration. Verify every process uses the same database; do not use separate ephemeral SQLite files on Cloud Run instances.
4. Check `/api/channels/telegram/health`. Inspect `worker_last_seen_seconds_ago`, `dedicated_workers_recent`, `jobs_by_status`, `oldest_pending_job_at`, `outbox_pending` and `outbox_dead`. These are aggregate observations, not an SLA. Alert on missing/stale workers, growing queue age and terminal failures.
5. Rehearse fresh Tamil/Telugu voice, location correction, optional ward, consent, duplicate clicks, private tracking, a worker restart, and a provider outage. Retain the deployment revision, operation evidence and measured acknowledgement/processing/delivery times.
6. To roll back, stop new workers and webhook traffic first. Preserve pending jobs for recovery; an older application cannot process this new queue. Do not drop queue tables or silently discard accepted references.

## Durability and limits

- Webhook event, draft changes, queued work and outbound acknowledgement are committed together. A short per-chat database lock serializes conversation changes.
- Each intake job reserves a ticket reference before AI processing. Retries use that reference; the citizen request's unique ID prevents duplicate tickets. Recovery checks for a saved ticket before running intake again. Downstream replication/audit side effects still need reconciliation if a process dies after the ticket save.
- Jobs use owner-tagged leases, default 900 seconds (`TELEGRAM_JOB_LEASE_SECONDS`, minimum 300). Expired leases can be reclaimed; stale owners cannot complete queue/session updates. Lease length must exceed the measured processing budget. Provider calls may repeat after an ambiguous crash; exactly-once billing is not guaranteed.
- Intake failures retry up to three normal processing attempts, then retain the job for operator investigation. Process crashes can cause additional lease recoveries. No automated operator notification or administrative retry UI is claimed. Operators should inspect the retained reference, resolve the cause and use a reviewed recovery procedure; do not ask the citizen to create a duplicate.
- Outbound messages have stable keys, atomic claims, expired-send recovery and bounded retries. Delivery is at least once: if Telegram accepted a message but its response was lost, retry can deliver it again. Failed messages reaching the attempt limit are visible as `outbox_dead`.
- Draft-bound buttons reject old draft identifiers. Status results are scoped to the sending Telegram account. Legacy unscoped buttons are retained for compatibility; full invalidation of older message keyboards is not claimed.
- Queue payloads contain private draft text and Telegram identifiers. Restrict database/operator access and apply the programme's retention/deletion policy. This implementation does not add an automatic queue-retention policy or encrypt individual payload fields.
- Local tests do not verify live Google availability, real Telegram delivery, PostgreSQL concurrency under production load, cloud worker configuration or recovery time. Record those separately before claiming operational readiness.

## Local checks

```sh
python -B -m pytest tests/test_speech_fallback.py tests/test_telegram_async.py tests/test_telegram_bot.py -q -p no:cacheprovider
```

Tests use isolated databases and mocked providers. They do not send Telegram messages or incur Google AI charges.
