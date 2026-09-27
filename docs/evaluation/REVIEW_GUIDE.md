# Independent multilingual review

The 468 developer-curated text cases in [review-pack-v3.json](review-pack-v3.json) cover 13 languages (36 cases each, with Tamil-first priority), 10 civic categories and three urgency tiers. The dataset includes 143 emergency cases, 11 per language; its labels remain provisional pending independent review. Dataset coverage is distinct from evaluated coverage: [benchmark-v3-quality.json](benchmark-v3-quality.json) currently records a 26-case live-mode run, with two Water Supply cases per language (one Urgent and one Emergency). It reports 100% category accuracy, 1.0000 category Macro-F1, 65.38% urgency accuracy and emergency recall of 13/13. Per-case provider, served-model and fallback evidence is absent from the aggregate report, so successful Google execution for every case remains unverified. These results do not establish full-dataset performance, Speech-to-Text accuracy or translation fidelity. [baseline-quality.json](baseline-quality.json) records the earlier local classifier on 108 v2 cases across English, Tamil and Telugu, with 0.3764 category Macro-F1; it is not directly comparable with the current v3 subset.

## Review procedure

1. Assign a fluent reviewer for each language and a second reviewer to adjudicate disagreements. Compute Cohen's Kappa ($\kappa$) inter-rater agreement across categories and urgency tiers. Record reviewer identity, date and relevant language competence. Do not invent these fields.
2. Review the source text before looking at model predictions. Label service category, urgency and explicit district/ward. Mark ambiguous or multi-issue cases for adjudication rather than forcing certainty. In particular, independently examine the sewage urgency disagreement and Tamil service-centre/code-switching case.
3. Compare the provider translation with the source. Score meaning preservation from 1 (materially wrong) to 5 (complete); separately record preserved location, negation, numbers and emergency intent. Save a corrected translation and explain material errors.
4. Keep proposed labels, reviewer labels and adjudicated labels separately. Record agreement/disagreement and abstention counts. Recompute category macro-F1, urgency confusion and emergency recall only for the corresponding labelled sample. Publish per-language denominators.
5. Freeze the adjudicated pack and hash it. Any model tuned against these 468 cases needs a new unseen holdout before reporting independent generalization.

A review record can use this shape:

```json
{
  "case_id": "TA08",
  "reviewer": null,
  "reviewed_at": null,
  "category": null,
  "urgency": null,
  "district": null,
  "translation_fidelity_1_to_5": null,
  "location_preserved": null,
  "negation_preserved": null,
  "corrected_translation": null,
  "notes": null
}
```

## Additional evaluation gates

| Gate | Data needed | Report |
|---|---|---|
| Speech recognition | Consented human recordings, independent verbatim transcripts, language/channel/noise labels; include dialects and code-switching | WER/CER with stated normalization, meaning-critical errors, sample sizes and fallback rate |
| Location extraction | Independently labelled explicit and ambiguous districts/wards, checked boundary crosswalk | Exact accuracy, ambiguity/abstention and wrong-district rate |
| Duplicate clustering | Human-labelled positive/negative pairs across channels and languages; avoid trivial same-template pairs | Pair precision/recall, false merges, false splits and reviewer agreement |
| Source authenticity | Publisher document/table, observation year, retrieval date, license and actual file hash | Verified/unverified/missing per field; checked geographic joins |
| Operational scale | Isolated deployment, declared workload/concurrency, worker telemetry and cloud billing | p50/p95/p99, throughput, failures, oldest queue age, retries and cost per request |

The current reports explicitly leave these fields unmeasured. Synthetic text/audio and automatic script checks cannot replace human review or field evidence.

## Reproduction

Run these commands from the repository root. The evaluator defaults to local simulation against the full v3 text dataset; it does not call Google unless `--live` is supplied. Use a separate `--output` path because the default output overwrites `docs/evaluation/benchmark-v3-quality.json` in either mode.

```sh
python scripts/evaluate_multilingual_benchmark.py --output scratch/evaluation/benchmark-v3-simulation.json
```

To repeat the current 26-case sampling configuration using the Google AI client:

```sh
python scripts/evaluate_multilingual_benchmark.py --live --sample-per-lang 2 --output scratch/evaluation/benchmark-v3-live-sample.json
```

Live mode requires Google credentials and may incur provider charges. The evaluator reads `GOOGLE_AI_API_KEY` or `GEMINI_API_KEY`, then attempts its configured Secret Manager lookup if neither is set. The client can fall back to local classification. Repeating the command does not guarantee identical scores or successful Google execution for every case.

There is no `--provider` option. The simulation command evaluates v3 and does not reproduce or replace the historical 108-case v2 `baseline-quality.json`. Both commands classify text without submitting citizen requests; they do not independently evaluate speech recognition or translation fidelity. Recorded live-mode elapsed time includes the evaluator's delay (default 0.25 seconds per case), any retries and client processing, so it is not isolated model latency.

The current report saves aggregate and per-language metrics but omits per-case predictions and provider/fallback provenance. Its `live_verified` field reflects the `--live` flag, not verified execution. A provider-verified evaluation needs retained case IDs, actual served models, fallback status and execution evidence in addition to the input hash and aggregate scores.

`python scripts/benchmark_analyst.py --rows 100000 --requests 20` operates on an isolated SQLite copy. Run it without concurrent browser/test workloads. Small-sample tail percentiles are descriptive; use a longer deployment soak for capacity planning.
