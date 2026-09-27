"""Evaluate the 468-case 13-language held-out benchmark.
Measures category macro-F1, urgency accuracy, and 1:1:1 life-safety emergency recall.
"""
import collections
import hashlib
import json
import os
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

def calculate_metrics(rows):
    labels = sorted({r['expected_category'] for r in rows} | {r['category'] for r in rows})
    f1 = []
    for label in labels:
        tp = sum(r['category'] == label and r['expected_category'] == label for r in rows)
        fp = sum(r['category'] == label and r['expected_category'] != label for r in rows)
        fn = sum(r['category'] != label and r['expected_category'] == label for r in rows)
        score = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0
        f1.append(score)

    emergencies = [r for r in rows if r['expected_urgency'] == 'Emergency']
    emergency_tp = sum(r['urgency'] == 'Emergency' for r in emergencies)
    emergency_fn = sum(r['urgency'] != 'Emergency' for r in emergencies)
    emergency_recall = (emergency_tp / len(emergencies)) if emergencies else 1.0

    category_correct = sum(r['category'] == r['expected_category'] for r in rows)
    urgency_correct = sum(r['urgency'] == r['expected_urgency'] for r in rows)

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
    pack_path = ROOT / 'docs/evaluation/review-pack-v3.json'
    pack = json.loads(pack_path.read_text(encoding='utf-8'))
    cases = pack['cases']

    print(f"Evaluating {len(cases)} cases across {len(pack['languages'])} languages...")
    rows = []

    for case in cases:
        start = time.perf_counter()
        pred = simulate_gemini_intent_classification(case['text'], case['language'], CATEGORIES)
        latency = round((time.perf_counter() - start) * 1000, 2)

        row = {
            'id': case['id'],
            'language': case['language'],
            'district': case['district'],
            'expected_category': case['category'],
            'expected_urgency': case['urgency'],
            'category': pred.get('category'),
            'urgency': pred.get('urgency'),
            'sentiment': pred.get('sentiment'),
            'latency_ms': latency
        }
        rows.append(row)

    overall = calculate_metrics(rows)
    by_language = {}
    for lang in pack['languages']:
        lang_rows = [r for r in rows if r['language'] == lang]
        by_language[lang] = calculate_metrics(lang_rows)

    report = {
        'benchmark_version': pack['version'],
        'evaluated_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': hashlib.sha256(pack_path.read_bytes()).hexdigest(),
        'overall': overall,
        'by_language': by_language,
        'target_thresholds': {
            'emergency_recall_minimum': 1.0,
            'category_macro_f1_minimum': 0.95,
            'status': 'PASSED' if (overall['emergency_recall'] == 1.0 and overall['category_macro_f1'] >= 0.95) else 'NEEDS_TUNING'
        }
    }

    out_file = ROOT / 'docs/evaluation/benchmark-v3-quality.json'
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

    print("\n" + "="*50)
    print("       MULTILINGUAL BENCHMARK EVALUATION RESULTS      ")
    print("="*50)
    print(f"Total Cases:            {overall['total_samples']}")
    print(f"Category Macro-F1:      {overall['category_macro_f1']:.4f} (Target: >= 0.9500)")
    print(f"Category Accuracy:      {overall['category_accuracy'] * 100:.2f}%")
    print(f"Urgency Accuracy:       {overall['urgency_accuracy'] * 100:.2f}%")
    print(f"Emergency Parity Cases: {overall['emergency_samples']}")
    print(f"Emergency Recall:       {overall['emergency_recall'] * 100:.2f}% ({overall['emergency_correct']}/{overall['emergency_samples']})")
    print(f"Emergency False Neg:    {overall['emergency_false_negatives']}")
    print(f"Threshold Verification: {report['target_thresholds']['status']}")
    print("="*50)

    print("\nPer-Language Emergency Recall and Macro-F1 Breakdown:")
    for lang, metrics in by_language.items():
        print(f"  [{lang:>2}] F1: {metrics['category_macro_f1']:.4f} | Urgency Acc: {metrics['urgency_accuracy']*100:.1f}% | Emerg Recall: {metrics['emergency_recall']*100:.1f}% ({metrics['emergency_correct']}/{metrics['emergency_samples']})")

    if overall['emergency_recall'] < 1.0 or overall['category_macro_f1'] < 0.95:
        print("\n[WARNING] Criteria not met. Inspect misclassified rows:")
        for r in rows:
            if r['expected_urgency'] == 'Emergency' and r['urgency'] != 'Emergency':
                print(f"  EMERGENCY FN: {r['id']} ({r['language']}) expected Emergency, got {r['urgency']}")
            if r['expected_category'] != r['category']:
                print(f"  CAT MISMATCH: {r['id']} ({r['language']}) expected {r['expected_category']}, got {r['category']}")

if __name__ == '__main__':
    main()
