# Ministry Pilot channel connections

The citizen home, full submission form, officer workspace and Settings live at
`/pilot`, `/pilot/submit`, `/pilot/dashboard` and `/pilot/settings`.

## Configure a provider

1. Set the channel owner, public address and enabled state in Settings → Channels & communication.
2. Inject `PILOT_CHANNEL_WHATSAPP_SECRET`, `PILOT_CHANNEL_TELEGRAM_SECRET`,
   `PILOT_CHANNEL_IVR_SECRET`, `PILOT_CHANNEL_SMS_SECRET` or `PILOT_CHANNEL_EMAIL_SECRET`
   from the deployment's managed secrets. Use a distinct secret for each channel.
3. Connect the provider's verified gateway to `POST /api/v2/pilot/channels/{channel}/webhook`.
   These are **normalized gateway contracts**, not drop-in native Meta, Telegram,
   Twilio or email-provider webhooks. The provider gateway must authenticate native
   callbacks and normalize their events. Never route pilot messages through legacy
   unscoped `/api/channels/` ingestion.
4. Resolve the participating community using `/api/v2/pilot/public-config`.
   Obtain explicit consent in the citizen's language before admission. The gateway
   must preserve the provider event ID across retries.
5. Deliver the returned receipt privately through the originating conversation.
   Send the signed delivery confirmation described below only after the provider
   confirms delivery. The homepage then exposes the channel link. Changing its
   address, owner or enabled state invalidates that recorded connection test.

## Signed inbound contract

Headers:

```
Content-Type: application/json
X-Pilot-Timestamp: <UTC Unix seconds, within five minutes>
X-Pilot-Signature: <hex HMAC-SHA256(secret, timestamp + "." + exact HTTP body bytes)>
```

Example body (use synthetic content for rehearsal):

```json
{
  "event_id": "stable-provider-message-id",
  "location_id": "vellore-1",
  "language": "ta",
  "text": "குடிநீர் தினமும் கிடைப்பதில்லை.",
  "consent_granted": true,
  "tracking_secret": "gateway-generated-random-code-at-least-32-characters"
}
```

The programme is selected by the deployment, not by the inbound body. Location
and language must be enrolled. Text, audio (`audio_base64`, `mime_type`) and
evidence (`evidence: [{name, mime_type, data}]`) use the same validated intake as
the public form. Download native provider media in the authenticated gateway,
apply the advertised limits, and normalize bytes; do not pass arbitrary download
URLs to NVB. Audio is limited to 3 MiB. Evidence is limited to 15 files, 5 MiB per
file and 8 MiB total. Provider retries with changed content are rejected.

Successful admission returns the durable request ID, private tracking code,
processing status and a `reply` object. Acknowledgement precedes AI completion.
Provide a stable randomly generated tracking code to recover the same receipt
after a lost HTTP response. Do not log the private code or put it in a URL.

After confirmed receipt delivery, send a separately signed body:

```json
{"action":"delivery_receipt","event_id":"stable-provider-message-id","delivered":true}
```

This records gateway-attested delivery, not independent provider certification.
The receiving gateway is part of the trusted deployment boundary. Receipt tests
must be repeated when its channel configuration changes.

Private tracking uses a signed body:

```json
{"action":"track","event_id":"stable-status-message-id","request_id":"NVB-YYYYMMDDXXXX","tracking_secret":"private-code"}
```

The gateway may poll this endpoint after a verified citizen opt-in to deliver
status updates. Outbound subscriptions and provider delivery are the gateway's
responsibility; NVB does not claim that a configured link alone delivers updates.

## Google services and deployment

Settings lists service owners, references, environment requirements and whether
an application client is loaded. Credentials and cloud resource creation remain
deployment operations. Live Gemini previews require `PILOT_MODEL_CALLS=true`,
the approved regional Vertex client and explicit citizen processing consent.
Voice uses the regional Speech v2 adapter. The durable worker continues to support
manual review when a provider is unavailable. Existing Cloud Tasks, Scheduler,
private audio storage and redacted BigQuery synchronization remain authoritative
for their respective operational tasks.

No live provider accounts, cloud resources, numbers or charges are created by
saving portal configuration. Run the deployment runbook and language/provider
evaluations before operational activation.

## Schema and rollout

Run the existing `pilot-migrate` command to apply the additive evidence, delivery
and channel-event tables. Evidence uploads retain private bytes in the operational
database and are downloadable only by authorised scoped officers. Include that
database in backup, retention and recovery controls. The existing private Cloud
Storage path continues to archive audio when configured; the new photo/document
records are not automatically migrated to an object bucket.

`python scripts/preview_pilot_portal.py` starts an isolated local rehearsal on
`http://127.0.0.1:5082/pilot`, with external services disabled and a separate
database under `scratch/portal-preview`.
