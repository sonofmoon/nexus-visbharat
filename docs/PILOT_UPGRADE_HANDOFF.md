# Pilot portal upgrade and judging handoff

This upgrade strengthens the local hackathon demonstration. It does not claim an award, government participation, deployment, independent field validation or successful live Google execution.

## Changes

- Preserve the authenticated officer review transition: a reconstructed fictional record stays synthetic, gains an actor/time/rationale, and becomes eligible after review. Category and department update together.
- Use the same planning eligibility rules for candidate counts and supporting request IDs in exports. JSON whitespace no longer affects quarantine. Pending processing, restricted records, emergency reports and unreviewed reconstructions are excluded appropriately. Emergency exclusions are distinguished from review/restriction exclusions.
- Require independent gateway receipt authentication before enabling an external channel link. A local connector test and the old `live_provider_verified` boolean cannot enable it. Changing channel configuration invalidates old evidence. Disabled channels have no public launch link.
- Add prepared Tamil/Vellore/water, Telugu/Tirupati/sanitation and Kannada/Bengaluru Urban/road examples to the synthetic Pilot home page. The examples prefill the enrolled location, language, category, fictional request and team-prepared English translation. They do not submit automatically or preselect consent. Examples are hidden for operational programmes and when their language, category or location is unavailable.
- Add an isolated rehearsal runner that exercises intake, district review, proposal, synthetic approval, commitment recognition, denied cross-district review and redacted audit export for all three districts.

## Demonstration route

1. Open the Main Portal and enter the Pilot Portal (`/pilot`). Keep the national corpus explanation separate from the bounded pilot.
2. Under **Three districts. Three development needs**, choose one prepared example.
3. Review the original text, team-prepared English translation, enrolled community and category; explicitly give consent and submit. Save the private receipt privately.
4. Follow the officer workspace link; use the appropriate district demonstration role and review the request.
5. Open Development & Policy, select the project and save a proposal. Use the Admin demonstration role for an illustrative engineering review and synthetic approval.
6. Refresh the planning scenario to show the existing commitment. This demonstrates internal commitment recognition, not verification against a government investment database.
7. Switch to another district officer and demonstrate denied access. Use Auditor to export the redacted dossier.

The prepared examples cover three categories; the portal still supports all enabled service categories. Water Supply is not a restriction on citizen requests.

## Reproducible local package

Run from the repository root:

```powershell
python -B scripts/rehearse_pilot_judging.py --output scratch/judging-run-01 --serve
```

Open `http://127.0.0.1:5002/pilot` while that process is running. The generated entry guide contains the exact completed-ticket routes for that isolated database. Choose a new output folder for each run. Credentials are generated for this local rehearsal and are not written to the exported package. The demo portal exposes role tokens by its existing demo design; do not use demo mode for an operational deployment.

The checked-in `docs/evaluation/pilot-upgrade-rehearsal` package records a completed local run and includes three redacted dossiers, an entry guide and a manifest. It uses a separate database under ignored `scratch/`; existing `visbharat.db` tickets are not changed. Generated ticket links only work against their corresponding isolated database.

## Messaging configuration

1. Configure a real channel address and enable it in Pilot Settings. Keep it unavailable publicly until delivery evidence passes.
2. Provision `PILOT_CHANNEL_<CHANNEL>_SECRET` for the existing normalized inbound gateway. Do not enter secrets in the portal.
3. Provision a different `PILOT_CHANNEL_<CHANNEL>_RECEIPT_SECRET` only for the trusted receipt-verification gateway. Never give it to clients or a rehearsal harness connected to the actual portal.
4. The gateway must validate the provider's native receipt/authentication and bind the outbound acknowledgement to the original inbound event before attesting delivery. Store the original provider evidence in the operator's controlled evidence store.
5. Send a receipt to `/api/v2/pilot/channels/<channel>/webhook` with the original `event_id`, `action: delivery_receipt`, `delivered: true`, `provider`, `provider_message_id`, `receipt_id` and `config_hash`. The config hash is the SHA-256 digest from `pilot.digest` of that channel's effective settings (enabled, address, owner).
6. Include the existing inbound signature and a separate `X-Pilot-Receipt-Signature`. Each is a hexadecimal HMAC-SHA256 over `timestamp + '.' + raw request body`, using its respective key. `X-Pilot-Timestamp` must be within five minutes. The body is signed byte-for-byte.

The portal verifies the gateway attestation; it does not implement native receipt verification for every provider. No real transaction was sent by this upgrade. Tests use fixture keys only in disposable databases. A simple signed local receipt remains useful as a connector rehearsal, but cannot open the public channel.

## Evidence still required

- **Google AI:** run a fresh request through the configured Google service and retain the actual provider/model, latency, response/usage where available, and fallback status. Existing synthetic test-harness records are not evidence of live Google calls.
- **Voice:** a team-recorded Tamil or Telugu fictional request, processed through the real configured speech service. A prepared written example is not a recording or speech test.
- **Messaging:** a controlled real-account transaction with original provider receipt and corresponding ticket. No government partnership is required for this test.
- **Deployment:** deploy the reviewed build and repeat the entry route on the deployed URL. This change has not been deployed.
- **Government pilot:** review official geography, datasets, agency responsibilities, identities, operational approvals and outcome measurements with the participating authority. Synthetic role actions and illustrative estimates are not evidence of these.

## Validation boundaries

Regression coverage is in `tests/test_pilot_upgrade.py`, `tests/test_pilot_portal.py` and `tests/test_pilot_portal_browser.py`; existing analyst and ministry tests cover surrounding workflows. The local evidence run uses SQLite and disabled external services. PostgreSQL and real cloud/gateway execution require their configured environments.

Latest local validation: **63 workflow/API tests passed, 4 browser tests passed, and all 3 isolated district journeys completed**. See [the validation record](evaluation/pilot-upgrade-rehearsal/VALIDATION.md) and [the generated manifest](evaluation/pilot-upgrade-rehearsal/manifest.json).
