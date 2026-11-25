# Black Shark provider rerun — 2026-11-24

- Same saved question set: 100 questions.
- Provider: Gemini; models: `{'gemini-3.1-flash-lite': 162, 'gemini-3.5-flash-lite': 184}`.
- Questions were sent sequentially; no question workers ran in parallel. Test harness pacing did not cover every internal AI stage/retry, so do not treat this as verified per-key RPM/TPM compliance.
- Usage events: 346; AI Usage Hub accepted: 346.
- Provider events included 47 HTTP 503 and 1 HTTP 429. They are recorded as failed attempts, not omitted. Usage events do not expose API-key identifiers or timestamps, so per-key RPM/TPM compliance cannot be verified from this run.
- Tokens: 1,025,233 prompt tokens and 38,591 completion tokens across the recorded events (not a per-minute measurement).
- Price: unavailable in captured event payload (`cost_usd` absent), so this dataset cannot show a dollar total.
- Shop scope check: 100/100; semantic accuracy is not claimed for all rows. See `manual_review.json` and `report.html`.
- Q012 produced an answer with zero captured provider events.
- No application source files were modified for this test run.

Files: `results.json`, `comparison.json`, `usage_events.json`, `summary.json`, `manual_review.json`, and filterable `report.html`.
