"""Provisional challenge evaluation. Does not submit or mutate citizen requests."""
import argparse
import collections
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from visbharat.services.ai_simulation import simulate_gemini_intent_classification


def metrics(rows):
    labels=sorted({r['expected_category'] for r in rows}|{r['category'] for r in rows})
    f1=[]
    for label in labels:
        tp=sum(r['category']==label and r['expected_category']==label for r in rows)
        fp=sum(r['category']==label and r['expected_category']!=label for r in rows)
        fn=sum(r['category']!=label and r['expected_category']==label for r in rows)
        f1.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)
    emergencies=[r for r in rows if r['expected_urgency']=='Emergency']
    confusion=collections.Counter(r['expected_urgency']+' -> '+r['urgency'] for r in rows)
    return {'samples':len(rows),'category_macro_f1':round(sum(f1)/len(f1),4) if f1 else None,
            'urgency_accuracy':round(sum(r['urgency']==r['expected_urgency'] for r in rows)/len(rows),4) if rows else None,
            'emergency_recall':round(sum(r['urgency']=='Emergency' for r in emergencies)/len(emergencies),4) if emergencies else None,
            'emergency_samples':len(emergencies),'urgency_confusion_matrix':dict(confusion),
            'fallback_count':sum(r['fallback'] for r in rows),'median_latency_ms':sorted(r['latency_ms'] for r in rows)[len(rows)//2] if rows else None}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--provider',choices=['baseline','google'],default='baseline');parser.add_argument('--limit',type=int,default=108);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];pack=json.loads((root/'docs/evaluation/review-pack.json').read_text(encoding='utf-8'))
    client=None
    if args.provider=='google':
        from visbharat.config import Config
        from visbharat.services.google_ai import GoogleAIClient
        if (root/'sa-key.json').exists() and not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            os.environ['GOOGLE_APPLICATION_CREDENTIALS']=str(root/'sa-key.json')
        client=GoogleAIClient(api_key=Config.GOOGLE_AI_API_KEY,project_id=Config.VERTEX_PROJECT_ID,location=Config.VERTEX_LOCATION,use_vertex=getattr(Config,'USE_VERTEX_FOR_GEMINI',False))
    rows=[];random.seed(20260919)
    categories=['Water Supply','Sanitation','Electricity','Road','Education','Health','Transport','Digital Connectivity','Housing','Other']
    # Interleave languages so a bounded live run still covers every language.
    cases=sorted(pack['cases'],key=lambda r:(r['id'][2:],r['language']))[:args.limit]
    for case in cases:
        started=time.perf_counter();translated=None;translation_fallback=False
        if client:
            text=case['text']
            if case['language']!='en':
                trans=client.translate_text(text,case['language']);text=trans['translated_text'];translated=text;translation_fallback=trans.get('fallback_used',False)
            prediction=client.classify_request(text,'en',categories)
        else:prediction=simulate_gemini_intent_classification(case['text'],case['language'],categories)
        row={'id':case['id'],'language':case['language'],'expected_category':case['category'],'expected_urgency':case['urgency'],
             'category':prediction.get('category','Unknown'),'urgency':prediction.get('urgency','Unknown'),
             'model':prediction.get('model'),'fallback':bool(prediction.get('fallback_used') or translation_fallback or not client),
             'translated_text':translated,'latency_ms':round((time.perf_counter()-started)*1000,2),'human_review':None}
        rows.append(row);print(json.dumps({k:row[k] for k in ('id','model','fallback','category','urgency','latency_ms')}),flush=True)
        # Preserve progress if a provider becomes unavailable.
        (root/'docs/evaluation/predictions-progress.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'status':'provisional_labels_require_human_review','provider':args.provider,'dataset':pack['version'],
            'generated_at':datetime.now(timezone.utc).isoformat(),
            'input_sha256':hashlib.sha256((root/'docs/evaluation/review-pack.json').read_bytes()).hexdigest(),
            'independent_human_review':False,'overall':metrics(rows),'by_language':{lang:metrics([r for r in rows if r['language']==lang]) for lang in sorted({r['language'] for r in rows})},
            'predictions':rows,'not_measured':{'asr_word_error_rate':'No independently transcribed noisy-audio corpus supplied','translation_fidelity':'Human review pending','location_accuracy':'Independent extraction annotations pending','duplicate_cluster_precision_recall':'Independent issue-pair review pending'},
            'limitations':['Small authored holdout with proposed labels, not a validated population benchmark.','Fixture labels and script checks are not human evaluation.','Fallback results are never counted as live-provider successes.']}
    filename='quality.json' if args.provider=='google' else 'baseline-quality.json'
    (root/'docs/evaluation'/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['overall']))

if __name__=='__main__':main()
