# Local Fast-Path Microbenchmark Report

- **Evaluation Date**: 2026-09-21 08:53:11 UTC
- **Workload**: 600 requests across 4 Indic language families (English, Tamil, Telugu, Hindi)
- **Concurrency**: 16 worker threads
- **Accuracy**: **100.0%** (Deterministic classification recall & precision)
- **Throughput**: **10259.0 requests/second**

---

## 1. Measured Empirical Latency Distribution

| Percentile | Triage Engine Internal Latency (`time.perf_counter()`) | Thread Pool Call Latency |
| :--- | :--- | :--- |
| **Min** | `0.001 ms` | `0.0035 ms` |
| **p50 (Median)** | `0.021 ms` | `0.024 ms` |
| **p90** | `0.0423 ms` | `0.0472 ms` |
| **p95** | `0.0505 ms` | `0.0562 ms` |
| **p99** | `0.075 ms` | `0.0837 ms` |
| **Max** | `0.291 ms` | `12.3728 ms` |

---

## 2. Scope and Limits

1. `triage_latency_ms` is measured per invocation with CPU monotonic time (`time.perf_counter()`), not a static timer.
2. This is an in-process Python microbenchmark. It excludes HTTP, Cloud Run scheduling, database persistence, DDMA delivery, acknowledgement, and external network latency.
3. It is not a production availability or end-to-end latency claim. A deployed Cloud Run load test is required before setting an operational SLO.

```json
{
  "total_requests": 600,
  "concurrency_workers": 16,
  "wall_clock_seconds": 0.0585,
  "throughput_req_per_sec": 10259.0,
  "accuracy_rate_pct": 100.0,
  "end_to_end_call_latency_ms": {
    "min": 0.0035,
    "p50": 0.024,
    "p90": 0.0472,
    "p95": 0.0562,
    "p99": 0.0837,
    "max": 12.3728,
    "mean": 0.0499
  },
  "internal_triage_latency_ms": {
    "min": 0.001,
    "p50": 0.021,
    "p90": 0.0423,
    "p95": 0.0505,
    "p99": 0.075,
    "max": 0.291,
    "mean": 0.0248
  }
}
```
