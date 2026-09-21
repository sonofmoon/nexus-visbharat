"""
Fast-Path Concurrency and Telemetry Benchmark
Executes concurrent load testing against deterministic fast-path emergency triage
to measure empirical latency percentiles (min, p50, p95, p99, max) and throughput.
"""

import concurrent.futures
import json
import math
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from visbharat.services.pipeline_queue import fast_path_emergency_triage


BENCHMARK_CASES = [
    # English
    ("Live wire hanging on the street near school gate!", "en", True),
    ("Severe gas leak reported near hospital kitchen cylinder explosion risk", "en", True),
    ("Bridge collapsed due to flash flood river overflowing", "en", True),
    ("Poisoned water toxic contamination reported in main overhead tank", "en", True),
    ("Routine street cleaning required in ward 4", "en", False),
    ("Street light flickering outside house number 12", "en", False),
    # Tamil
    ("மின்சாரக் கம்பி அறுந்து விழுந்துவிட்டது, உடனடியாக வாருங்கள்", "ta", True),
    ("கேஸ் கசிவு சிலிண்டர் வெடிப்பு அபாயம் உள்ளது", "ta", True),
    ("பாலம் இடிந்து விழுந்துவிட்டது உடனடி ஆபத்து", "ta", True),
    ("குடிநீரில் சாக்கடை கலப்பு விஷ நீர் வருகிறது", "ta", True),
    ("சாலையில் குப்பை தேங்கியுள்ளது சுத்தம் செய்க", "ta", False),
    # Telugu
    ("విద్యుత్ తీగ తెగిపడిపోయింది ప్రమాదం", "te", True),
    ("గ్యాస్ లీకేజీ జరుగుతోంది తీవ్ర ప్రమాదం", "te", True),
    ("భవనం కూలిపోయింది సహాయం కావాలి", "te", True),
    ("రహదారి మరమ్మతులు చేయవలసిందిగా కోరుతున్నాము", "te", False),
    # Hindi
    ("बिजली का तार टूटकर सड़क पर गिर गया है तुरंत मदद चाहिए", "hi", True),
    ("गैस रिसाव हो रहा है आग लग सकती है", "hi", True),
    ("मकान गिर गया है लोग फंसे हैं", "hi", True),
    ("सड़क पर गड्ढा है इसे भरवाएं", "hi", False),
]


def run_single_triage(item):
    text, lang, expect_trigger = item
    t0 = time.perf_counter()
    result = fast_path_emergency_triage(text, lang)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    triggered = result is not None and result.get('fast_path_triggered', False)
    reported_latency_ms = result.get('triage_latency_ms') if result else elapsed_ms
    return {
        'elapsed_ms': elapsed_ms,
        'reported_latency_ms': reported_latency_ms,
        'triggered': triggered,
        'expected': expect_trigger,
        'correct': (triggered == expect_trigger),
    }


def compute_percentiles(values):
    if not values:
        return {}
    sorted_v = sorted(values)
    n = len(sorted_v)
    def _pct(p):
        k = (n - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_v[int(k)]
        return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)

    return {
        'min': round(sorted_v[0], 4),
        'p50': round(_pct(50), 4),
        'p90': round(_pct(90), 4),
        'p95': round(_pct(95), 4),
        'p99': round(_pct(99), 4),
        'max': round(sorted_v[-1], 4),
        'mean': round(sum(sorted_v) / n, 4),
    }


def run_benchmark(total_requests: int = 600, max_workers: int = 16):
    items = [BENCHMARK_CASES[i % len(BENCHMARK_CASES)] for i in range(total_requests)]
    print(f"Starting concurrency benchmark: {total_requests} requests across {max_workers} worker threads...")

    wall_start = time.perf_counter()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_single_triage, item) for item in items]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    wall_duration = time.perf_counter() - wall_start

    elapsed_list = [r['elapsed_ms'] for r in results]
    reported_list = [r['reported_latency_ms'] for r in results]
    correct_count = sum(1 for r in results if r['correct'])
    throughput = len(results) / wall_duration

    elapsed_pct = compute_percentiles(elapsed_list)
    reported_pct = compute_percentiles(reported_list)

    summary = {
        'total_requests': total_requests,
        'concurrency_workers': max_workers,
        'wall_clock_seconds': round(wall_duration, 4),
        'throughput_req_per_sec': round(throughput, 1),
        'accuracy_rate_pct': round((correct_count / total_requests) * 100.0, 2),
        'end_to_end_call_latency_ms': elapsed_pct,
        'internal_triage_latency_ms': reported_pct,
    }

    print("\n--- BENCHMARK RESULTS ---")
    print(f"Total Requests: {total_requests}")
    print(f"Workers: {max_workers}")
    print(f"Throughput: {summary['throughput_req_per_sec']} req/sec")
    print(f"Accuracy: {summary['accuracy_rate_pct']}%")
    print(f"Call Latency (ms): p50={elapsed_pct['p50']}, p95={elapsed_pct['p95']}, p99={elapsed_pct['p99']}, max={elapsed_pct['max']}")
    print(f"Triage Internal (ms): p50={reported_pct['p50']}, p95={reported_pct['p95']}, p99={reported_pct['p99']}")

    # Save to docs/evaluation/FASTPATH_CONCURRENCY_BENCHMARK.md
    out_dir = Path(__file__).resolve().parent.parent / "docs" / "evaluation"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / "FASTPATH_CONCURRENCY_BENCHMARK.md"

    md_content = f"""# Local Fast-Path Microbenchmark Report

- **Evaluation Date**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}
- **Workload**: {total_requests} requests across 4 Indic language families (English, Tamil, Telugu, Hindi)
- **Concurrency**: {max_workers} worker threads
- **Accuracy**: **{summary['accuracy_rate_pct']}%** (Deterministic classification recall & precision)
- **Throughput**: **{summary['throughput_req_per_sec']} requests/second**

---

## 1. Measured Empirical Latency Distribution

| Percentile | Triage Engine Internal Latency (`time.perf_counter()`) | Thread Pool Call Latency |
| :--- | :--- | :--- |
| **Min** | `{reported_pct['min']} ms` | `{elapsed_pct['min']} ms` |
| **p50 (Median)** | `{reported_pct['p50']} ms` | `{elapsed_pct['p50']} ms` |
| **p90** | `{reported_pct['p90']} ms` | `{elapsed_pct['p90']} ms` |
| **p95** | `{reported_pct['p95']} ms` | `{elapsed_pct['p95']} ms` |
| **p99** | `{reported_pct['p99']} ms` | `{elapsed_pct['p99']} ms` |
| **Max** | `{reported_pct['max']} ms` | `{elapsed_pct['max']} ms` |

---

## 2. Scope and Limits

1. `triage_latency_ms` is measured per invocation with CPU monotonic time (`time.perf_counter()`), not a static timer.
2. This is an in-process Python microbenchmark. It excludes HTTP, Cloud Run scheduling, database persistence, DDMA delivery, acknowledgement, and external network latency.
3. It is not a production availability or end-to-end latency claim. A deployed Cloud Run load test is required before setting an operational SLO.

```json
{json.dumps(summary, indent=2)}
```
"""
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"\nSaved benchmark report to: {report_file}")
    return summary


if __name__ == '__main__':
    run_benchmark(total_requests=600, max_workers=16)
