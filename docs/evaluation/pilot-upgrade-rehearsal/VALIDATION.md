# Local validation — 27 September 2026

Executed against the upgraded local source:

| Check | Result |
| --- | --- |
| Pilot upgrade, portal and analyst regression suites | 63 passed in 153.31 seconds |
| Headless Pilot browser suite | 4 passed in 34.71 seconds |
| Separate three-district API rehearsal | 3 completed journeys and redacted dossiers |
| Changed intake JavaScript syntax | Passed |
| Whitespace/error check on modified tracked source | Passed |

Commands:

```text
python -B -m pytest tests/test_pilot_upgrade.py tests/test_pilot_portal.py tests/test_analyst_workbench.py -q -p no:cacheprovider -x
python -B -m pytest tests/test_pilot_portal_browser.py -q -p no:cacheprovider -x
python -B scripts/rehearse_pilot_judging.py --output docs/evaluation/pilot-upgrade-rehearsal
node --check static/js/pilot-submit.js
```

The initial sandboxed test attempt failed because Windows denied access to its temporary database directories. The successful test runs used approved execution outside that sandbox, with disposable databases. One regression test setup was corrected to place the pending request in the same category as the reviewed request.

Browser coverage: citizen submission/private receipt tracking, officer workspace/settings, mobile district navigation, and prepared Bengaluru Kannada example submission with consent. External web requests were blocked in the browser tests.

The three API journeys exercised intake, scoped review, proposal, illustrative engineering review, synthetic approval, internal commitment recognition, cross-district access denial and redacted audit export. Original text and private tracking secrets were absent from the generated dossiers. The source portal database was not used by the runner.

These results do not establish live Google AI execution, real provider delivery, PostgreSQL deployment compatibility, government validation or production readiness. The messaging tests validate the independent receipt-signature contract using fixture keys, not an actual provider transaction.
