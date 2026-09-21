# Field Acceptance Protocol: 300-Sample Dialect Verification

**Document Version:** `v1.0-evaluator-calibration`  
**Governing Standard:** Public Grievance AI Reliability & Dialect Inclusion  
**Target:** Ministry Pilot Expansion Phase  

---

## 1. Executive Summary

While the provisional 27-case developer verification benchmark demonstrated feasibility (0.88 Macro F1, 100% emergency recall in synthetic tests), production deployment across Indian administrative zones requires an evaluator-grade **300-Sample Field Acceptance Protocol**.

This protocol defines the sampling strata, independent ground-truth adjudication process, and acceptance thresholds across 12 distinct dialect zones and 4 intake modalities.

---

## 2. Sampling Strata (300 Field Samples)

The 300 samples must be collected from real citizen interactions, gram panchayat sabha audio recordings, and field operator logs across three linguistic clusters:

### 2.1 Tamil Linguistic Zone (100 Samples)
- **Madras / Chennai Colloquial (25 samples):** Code-mixed Tanglish (`"road fulla potholes, water lora varave illa"`).
- **Kongu Dialect (Western TN) (25 samples):** Coimbatore/Erode agricultural and municipal grievances.
- **Vellore / North Arcot Transition (25 samples):** SBM-G sanitation and borewell complaints with Telugu lexical influence.
- **Nellai / Southern Colloquial (25 samples):** Southern Tamil idioms and rural drainage issues.

### 2.2 Telugu Linguistic Zone (100 Samples)
- **Rayalaseema Dialect (Tirupati / Chittoor) (35 samples):** Drought, borewell failure, and JJM pipe burst grievances.
- **Coastal Andhra (Krishna / Guntur) (35 samples):** Drainage overflow and canal embankment concerns.
- **Telangana / Hyderabad Colloquial (30 samples):** Dakhni-infused urban municipal code-mix.

### 2.3 Hindi / Dakhni / Border Corridors (100 Samples)
- **Dakhni Corridor (Bengaluru Rural / Border) (35 samples):** Tri-lingual code-switching (Kannada-Tamil-Hindi).
- **Bhojpuri / North Indian Migrant Ingress (35 samples):** Industrial corridor worker housing and potable water queries.
- **Standard Hindi Administrative (30 samples):** Pan-Indian grievance intake baseline.

---

## 3. Ground-Truth Adjudication Protocol

Each of the 300 samples must undergo double-blind review:
1. **Primary Annotator:** Certified native speaker / district public grievance desk officer.
2. **Secondary Annotator:** Senior administrative officer (Tahsildar / Block Development Officer level).
3. **Inter-Rater Reliability Threshold:** Cohen's Kappa \(\kappa \ge 0.85\). Any disagreement is resolved by a 3-member consensus board before comparing against VisBharat model predictions.

---

## 4. Production Go/No-Go Acceptance Gates

| Metric | Minimum Passing Threshold | Developer Benchmark (Provisional) | Field Target |
| :--- | :--- | :--- | :--- |
| **Category Macro F1** | \(\ge 0.82\) across all 12 dialect zones | 0.88 (27 cases) | \(\ge 0.85\) |
| **Emergency Hazard Recall** | \(\ge 0.98\) (Safety-critical fail-safe) | 1.00 (9 cases) | 1.00 (75 field emergency cases) |
| **Urgency Classification Accuracy** | \(\ge 0.88\) | 0.926 (27 cases) | \(\ge 0.90\) |
| **Emergency Fast-Path Latency** | \(< 500\text{ms}\) (p99) | < 25ms (deterministic) | \(< 100\text{ms}\) |
| **Full LLM Ingestion Latency** | \(< 4000\text{ms}\) (p90) | 11,553ms (unoptimized) | \(< 3000\text{ms}\) |
