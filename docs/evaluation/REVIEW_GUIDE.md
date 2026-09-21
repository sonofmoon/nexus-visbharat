# Independent multilingual review

The 27 cases in `review-pack.json` were newly authored for evaluation, independently of the demo templates. Their labels are provisional. `quality.json` records the live provider's responses; `baseline-quality.json` records the local fallback. Neither report establishes independent human validation.

## Review procedure

1. Assign a fluent reviewer for each language and a second reviewer to adjudicate disagreements. Record reviewer identity, date and relevant language competence. Do not invent these fields.
2. Review the source text before looking at model predictions. Label service category, urgency and explicit district/ward. Mark ambiguous or multi-issue cases for adjudication rather than forcing certainty. In particular, independently examine the sewage urgency disagreement and Tamil service-centre/code-switching case.
3. Compare the provider translation with the source. Score meaning preservation from 1 (materially wrong) to 5 (complete); separately record preserved location, negation, numbers and emergency intent. Save a corrected translation and explain material errors.
4. Keep proposed labels, reviewer labels and adjudicated labels separately. Record agreement/disagreement and abstention counts. Recompute category macro-F1, urgency confusion and emergency recall only for the corresponding labelled sample. Publish per-language denominators.
5. Freeze the adjudicated pack and hash it. Any model tuned against these 27 cases needs a new unseen holdout before reporting independent generalization.

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

`python scripts/evaluate_analyst_quality.py --provider google --limit 27` invokes the configured live classifier and translation provider without submitting citizen requests. Save the existing report before rerunning. `--provider baseline` evaluates fallback behavior and writes the baseline report; it does not overwrite the live report. The reports need to retain provider/model, sample IDs, input hash, time, fallbacks and limitations. Timing includes translation for native-language samples and is not a classifier-only comparison.

`python scripts/benchmark_analyst.py --rows 100000 --requests 20` operates on an isolated SQLite copy. Run it without concurrent browser/test workloads. Small-sample tail percentiles are descriptive; use a longer deployment soak for capacity planning.
