"""Evaluate the multilingual benchmark.
Measures category macro-F1, urgency accuracy, and life-safety emergency recall.
Supports both fast local simulation and live Google Gemini API evaluation via --live.
"""
import argparse
import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visbharat.services.ai_simulation import simulate_gemini_intent_classification

CATEGORIES = [
    'Water Supply', 'Road', 'Sanitation', 'Electricity', 'Health',
    'Education', 'Transport', 'Housing', 'Digital Connectivity', 'Other'
]


def discover_gemini_api_key(explicit_key: str = '') -> str:
    if explicit_key:
        return explicit_key
    env_key = os.getenv('GOOGLE_AI_API_KEY') or os.getenv('GEMINI_API_KEY')
    if env_key:
        return env_key
    # Try fetching from GCP Secret Manager
    try:
        gcloud_bin = shutil.which('gcloud') or 'gcloud.cmd'
        out = subprocess.check_output(
            [gcloud_bin, 'secrets', 'versions', 'access', 'latest',
             '--secret=nvb-google-ai-api-key', '--project=nexus-visbharat'],
            stderr=subprocess.DEVNULL
        )
        key = out.decode('utf-8').strip()
        if key:
            return key
    except Exception:
        pass
    return ''


def calculate_metrics(rows):
    labels = sorted({r['expected_category'] for r in rows} | {r['category'] for r in rows if r.get('category')})
    f1 = []
    for label in labels:
        tp = sum(r.get('category') == label and r['expected_category'] == label for r in rows)
        fp = sum(r.get('category') == label and r['expected_category'] != label for r in rows)
        fn = sum(r.get('category') != label and r['expected_category'] == label for r in rows)
        score = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0
        f1.append(score)

    emergencies = [r for r in rows if r['expected_urgency'] == 'Emergency']
    emergency_tp = sum(r.get('urgency') == 'Emergency' for r in emergencies)
    emergency_fn = sum(r.get('urgency') != 'Emergency' for r in emergencies)
    emergency_recall = (emergency_tp / len(emergencies)) if emergencies else 1.0

    category_correct = sum(r.get('category') == r['expected_category'] for r in rows)
    urgency_correct = sum(r.get('urgency') == r['expected_urgency'] for r in rows)

    return {
        'total_samples': len(rows),
        'category_accuracy': round(category_correct / len(rows), 4) if rows else 0,
        'category_macro_f1': round(sum(f1) / len(f1), 4) if f1 else 0,
        'urgency_accuracy': round(urgency_correct / len(rows), 4) if rows else 0,
        'emergency_samples': len(emergencies),
        'emergency_correct': emergency_tp,
        'emergency_false_negatives': emergency_fn,
        'emergency_recall': round(emergency_recall, 4),
        'median_latency_ms': sorted(r['latency_ms'] for r in rows)[len(rows) // 2] if rows else 0
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate multilingual benchmark on local simulation or live Gemini API")
    parser.add_argument('--live', action='store_true', help="Use live Google Gemini API (gemini-3.6-flash)")
    parser.add_argument('--api-key', default='', help="Google Gemini API key (defaults to environment or GCP Secret Manager)")
    parser.add_argument('--sample-per-lang', type=int, default=0, help="Evaluate N cases per language (stratified across routine/urgent/emergency)")
    parser.add_argument('--limit', type=int, default=0, help="Limit total cases evaluated")
    parser.add_argument('--delay', type=float, default=0.25, help="Delay in seconds between live API calls (default 0.25s)")
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluation/benchmark-v3-quality.json', help="Output report path")
    args = parser.parse_args()

    pack_path = ROOT / 'docs/evaluation/review-pack-v3.json'
    pack = json.loads(pack_path.read_text(encoding='utf-8'))
    cases = pack['cases']

    # Filter/sample if requested
    if args.sample_per_lang > 0:
        sampled = []
        for lang in pack['languages']:
            lang_cases = [c for c in cases if c['language'] == lang]
            # Pick balanced mix of emergency and non-emergency
            emerg = [c for c in lang_cases if c['urgency'] == 'Emergency']
            non_emerg = [c for c in lang_cases if c['urgency'] != 'Emergency']
            n_emerg = max(1, args.sample_per_lang // 2)
            n_non_emerg = max(1, args.sample_per_lang - n_emerg)
            sampled.extend(emerg[:n_emerg])
            sampled.extend(non_emerg[:n_non_emerg])
        cases = sampled
    elif args.limit > 0:
        cases = cases[:args.limit]

    live_client = None
    if args.live:
        from visbharat.services.google_ai import GoogleAIClient
        api_key = discover_gemini_api_key(args.api_key)
        if not api_key:
            print("[ERROR] --live specified but no Google AI API key found in environment or GCP Secret Manager.", flush=True)
            sys.exit(1)
        live_client = GoogleAIClient(api_key=api_key)
        print(f"[LIVE MODE] Evaluating {len(cases)} cases against Google Gemini (Model: gemini-3.6-flash)...", flush=True)
    else:
        print(f"[SIMULATION MODE] Evaluating {len(cases)} cases against local in-memory simulator...", flush=True)

    rows = []
    total = len(cases)
    for idx, case in enumerate(cases, 1):
        start = time.perf_counter()
        if live_client:
            max_retries = 3
            pred = {}
            for attempt in range(max_retries):
                try:
                    pred = live_client.classify_request(case['text'], language=case['language'], categories=CATEGORIES)
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"  [ERROR] {case['id']} failed after {max_retries} attempts: {e}", flush=True)
                        pred = {'category': 'Other', 'urgency': 'Routine', 'sentiment': 'Neutral', 'fallback_used': True}
                    else:
                        time.sleep(2.0 * (attempt + 1))
            if args.delay > 0:
                time.sleep(args.delay)
        else:
            pred = simulate_gemini_intent_classification(case['text'], case['language'], CATEGORIES)

        latency = round((time.perf_counter() - start) * 1000, 2)
        match_cat = "OK" if pred.get('category') == case['category'] else f"MISMATCH ({pred.get('category')} != {case['category']})"
        match_urg = "OK" if pred.get('urgency') == case['urgency'] else f"MISMATCH ({pred.get('urgency')} != {case['urgency']})"

        if args.live or idx % 25 == 0 or idx == total:
            print(f"[{idx:>3}/{total}] [{case['language']:>2}] {case['id']}: Cat: {match_cat} | Urg: {match_urg} ({latency:.0f}ms)", flush=True)

        row = {
            'id': case['id'],
            'language': case['language'],
            'district': case['district'],
            'expected_category': case['category'],
            'expected_urgency': case['urgency'],
            'category': pred.get('category'),
            'urgency': pred.get('urgency'),
            'sentiment': pred.get('sentiment'),
            'confidence': pred.get('confidence'),
            'latency_ms': latency,
            'model': pred.get('model', 'local-simulator'),
            'provider_mode': pred.get('provider_mode', 'local_fallback'),
            'fallback_used': pred.get('fallback_used', False)
        }
        rows.append(row)

    overall = calculate_metrics(rows)
    evaluated_langs = sorted(list({r['language'] for r in rows}))
    by_language = {}
    for lang in evaluated_langs:
        lang_rows = [r for r in rows if r['language'] == lang]
        by_language[lang] = calculate_metrics(lang_rows)

    report = {
        'benchmark_version': pack['version'],
        'evaluated_at': datetime.now(timezone.utc).isoformat(),
        'evaluation_mode': 'google_ai_live' if args.live else 'local_simulation',
        'provider': 'Google Vertex / Gemini 3.6 Flash' if args.live else 'Local Simulated Heuristic Classifier',
        'live_verified': bool(args.live),
        'input_sha256': hashlib.sha256(pack_path.read_bytes()).hexdigest(),
        'overall': overall,
        'by_language': by_language,
        'target_thresholds': {
            'emergency_recall_minimum': 1.0,
            'category_macro_f1_minimum': 0.95,
            'status': 'PASSED' if (overall['emergency_recall'] == 1.0 and overall['category_macro_f1'] >= 0.95) else 'NEEDS_TUNING'
        }
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\nSaved report to: {args.output}")

    print("\n" + "="*50)
    print("       MULTILINGUAL BENCHMARK EVALUATION RESULTS      ")
    print("="*50)
    print(f"Mode:                   {'LIVE GOOGLE GEMINI (gemini-3.6-flash)' if args.live else 'LOCAL SIMULATION'}")
    print(f"Total Cases:            {overall['total_samples']}")
    print(f"Category Macro-F1:      {overall['category_macro_f1']:.4f} (Target: >= 0.9500)")
    print(f"Category Accuracy:      {overall['category_accuracy'] * 100:.2f}%")
    print(f"Urgency Accuracy:       {overall['urgency_accuracy'] * 100:.2f}%")
    print(f"Emergency Parity Cases: {overall['emergency_samples']}")
    print(f"Emergency Recall:       {overall['emergency_recall'] * 100:.2f}% ({overall['emergency_correct']}/{overall['emergency_samples']})")
    print(f"Emergency False Neg:    {overall['emergency_false_negatives']}")
    print(f"Median Latency:         {overall['median_latency_ms']:.2f} ms")
    print(f"Threshold Verification: {report['target_thresholds']['status']}")
    print("="*50)

    print("\nPer-Language Emergency Recall and Macro-F1 Breakdown:")
    for lang, metrics in by_language.items():
        print(f"  [{lang:>2}] F1: {metrics['category_macro_f1']:.4f} | Urgency Acc: {metrics['urgency_accuracy']*100:.1f}% | Emerg Recall: {metrics['emergency_recall']*100:.1f}% ({metrics['emergency_correct']}/{metrics['emergency_samples']})")


if __name__ == '__main__':
    main()
