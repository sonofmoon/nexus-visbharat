# NVB enterprise Gmail intake

The live Gmail path is:

`Gmail mailbox -> Gmail API watch -> Cloud Pub/Sub -> Cloud Run -> NVB ticket -> Gmail confirmation`

## Application configuration

Provision these values through Cloud Run environment configuration and Secret Manager. Never place mailbox credentials in the public page or source code.

```text
GMAIL_ENABLED=true
GMAIL_MAILBOX=<NVB Workspace mailbox>
GMAIL_AUTH_MODE=service_account
GMAIL_PUBSUB_TOPIC=projects/<project>/topics/<gmail-topic>
GMAIL_PUBSUB_AUDIENCE=https://<cloud-run-host>/api/channels/email/gmail/pubsub
GMAIL_PUBSUB_SERVICE_ACCOUNT=<pubsub-push-service-account>
GMAIL_SECRET_PROVIDER_ENABLED=true
GMAIL_SECRET_PROVIDER_PREFIX=visbharat/gmail
```

The Secret Manager prefix should provide `service_account_json` for Workspace domain-wide delegation. The delegated service account needs the minimum Gmail scopes required for mailbox reading and confirmation sending.

## Pub/Sub and watch lifecycle

Create a push subscription targeting:

`POST /api/channels/email/gmail/pubsub`

Configure Pub/Sub OIDC authentication with the service account named by `GMAIL_PUBSUB_SERVICE_ACCOUNT`. Run `gmail_watch_worker.py` once during activation and then at least daily; Gmail watches expire and must be renewed.

## Operational probes

- `GET /api/channels/email/gmail/health`
- `GET /api/channels/email/gmail/health` should report `enabled`, mailbox/topic configuration, and the stored watch cursor.

The database stores the Gmail history cursor and inbound Gmail message IDs, so Pub/Sub redelivery does not create duplicate NVB tickets.
