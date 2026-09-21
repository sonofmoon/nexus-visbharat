#!/usr/bin/env python3
import argparse
import json
import random
import sys
from collections import Counter

import requests

DEMO_SOURCE = "Demo Seeder"

TARGET_DISTRICTS = [
    # Tamil Nadu (38)
    ("Ariyalur", "ta"), ("Chengalpattu", "ta"), ("Chennai", "ta"), ("Coimbatore", "ta"), ("Cuddalore", "ta"),
    ("Dharmapuri", "ta"), ("Dindigul", "ta"), ("Erode", "ta"), ("Kallakurichi", "ta"), ("Kanchipuram", "ta"),
    ("Kanyakumari", "ta"), ("Karur", "ta"), ("Krishnagiri", "ta"), ("Madurai", "ta"), ("Mayiladuthurai", "ta"),
    ("Nagapattinam", "ta"), ("Namakkal", "ta"), ("Nilgiris", "ta"), ("Perambalur", "ta"), ("Pudukkottai", "ta"),
    ("Ramanathapuram", "ta"), ("Ranipet", "ta"), ("Salem", "ta"), ("Sivaganga", "ta"), ("Tenkasi", "ta"),
    ("Thanjavur", "ta"), ("Theni", "ta"), ("Thoothukudi", "ta"), ("Tiruchirappalli", "ta"), ("Tirunelveli", "ta"),
    ("Tirupathur", "ta"), ("Tiruppur", "ta"), ("Tiruvallur", "ta"), ("Tiruvannamalai", "ta"), ("Tiruvarur", "ta"),
    ("Vellore", "ta"), ("Viluppuram", "ta"), ("Virudhunagar", "ta"),
    # Andhra Pradesh (26)
    ("Alluri Sitharama Raju", "te"), ("Anakapalli", "te"), ("Ananthapuramu", "te"), ("Annamayya", "te"), ("Bapatla", "te"),
    ("Chittoor", "te"), ("Dr. B.R. Ambedkar Konaseema", "te"), ("East Godavari", "te"), ("Eluru", "te"), ("Guntur", "te"),
    ("Kakinada", "te"), ("Krishna", "te"), ("Kurnool", "te"), ("Nandyal", "te"), ("NTR", "te"), ("Palnadu", "te"),
    ("Parvathipuram Manyam", "te"), ("Prakasam", "te"), ("Sri Potti Sriramulu Nellore", "te"), ("Sri Sathya Sai", "te"),
    ("Srikakulam", "te"), ("Tirupati", "te"), ("Visakhapatnam", "te"), ("Vizianagaram", "te"), ("West Godavari", "te"), ("YSR Kadapa", "te"),
    # Telangana (33)
    ("Adilabad", "te"), ("Bhadradri Kothagudem", "te"), ("Hanamkonda", "te"), ("Hyderabad", "en"), ("Jagtial", "te"),
    ("Jangaon", "te"), ("Jayashankar Bhupalpally", "te"), ("Jogulamba Gadwal", "te"), ("Kamareddy", "te"), ("Karimnagar", "te"),
    ("Khammam", "te"), ("Kumuram Bheem Asifabad", "te"), ("Mahabubabad", "te"), ("Mahabubnagar", "te"), ("Mancherial", "te"),
    ("Medak", "te"), ("Medchal-Malkajgiri", "en"), ("Mulugu", "te"), ("Nagarkurnool", "te"), ("Nalgonda", "te"),
    ("Narayanpet", "te"), ("Nirmal", "te"), ("Nizamabad", "te"), ("Peddapalli", "te"), ("Rajanna Sircilla", "te"),
    ("Rangareddy", "en"), ("Sangareddy", "te"), ("Siddipet", "te"), ("Suryapet", "te"), ("Vikarabad", "te"),
    ("Wanaparthy", "te"), ("Warangal", "te"), ("Yadadri Bhuvanagiri", "te")
]

SAMPLES = {
    "ta": [
        "எங்கள் பகுதியில் சாலை சேதமாக உள்ளது, மழையில் பயணம் கடினமாகிறது.",
        "குடிநீர் விநியோகம் மூன்று நாட்களாக இல்லை.",
        "தெரு விளக்குகள் வேலை செய்யவில்லை, இரவில் பாதுகாப்பு பிரச்சனை உள்ளது.",
    ],
    "te": [
        "మా కాలనీలో రోడ్ పూర్తిగా దెబ్బతింది, వర్షంలో వెళ్లడం కష్టం.",
        "మూడు రోజులుగా తాగునీటి సరఫరా నిలిచిపోయింది.",
        "వీధి దీపాలు పనిచేయడం లేదు, రాత్రి భద్రత సమస్యగా ఉంది.",
    ],
    "en": [
        "The road in our area is badly damaged and causes daily traffic issues.",
        "Water supply has been interrupted for the last two days.",
        "Streetlights are not working and public safety is affected at night.",
    ],
}


def seed(base_url: str, per_district: int, timeout: int):
    submit_url = f"{base_url.rstrip('/')}/api/submit"
    submitted = []
    failures = []

    for district, language in TARGET_DISTRICTS:
        for _ in range(per_district):
            text = random.choice(SAMPLES[language])
            payload = {
                "text": text,
                "language": language,
                "district": district,
                "source": DEMO_SOURCE,
            }
            try:
                resp = requests.post(submit_url, json=payload, timeout=timeout)
                if resp.status_code == 200 and resp.json().get("success"):
                    submitted.append((district, language, resp.json().get("request_id")))
                else:
                    failures.append({
                        "district": district,
                        "language": language,
                        "status": resp.status_code,
                        "body": resp.text[:300],
                    })
            except Exception as err:
                failures.append({
                    "district": district,
                    "language": language,
                    "status": "exception",
                    "body": str(err),
                })

    counts = Counter([d for d, _, _ in submitted])

    print("\n=== VisBharath Demo Seeder ===")
    print(f"Base URL: {base_url}")
    print(f"Source tag: {DEMO_SOURCE}")
    print(f"Target districts: {', '.join([d for d, _ in TARGET_DISTRICTS])}")
    print(f"Submitted: {len(submitted)} | Failed: {len(failures)}")

    print("\nPer-district submitted count:")
    for district, _ in TARGET_DISTRICTS:
        print(f"- {district}: {counts.get(district, 0)}")

    if submitted:
        print("\nSample request IDs:")
        for _, _, request_id in submitted[:10]:
            print(f"- {request_id}")

    if failures:
        print("\nFailures:")
        print(json.dumps(failures[:10], indent=2, ensure_ascii=False))

    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser(description="Seed demo complaints for 6 target districts.")
    parser.add_argument("--base-url", default="http://localhost:5000", help="API base URL")
    parser.add_argument("--per-district", type=int, default=6, help="Complaints per district")
    parser.add_argument("--timeout", type=int, default=15, help="Request timeout in seconds")
    args = parser.parse_args()

    if args.per_district < 1:
        print("per-district must be >= 1", file=sys.stderr)
        return 2

    return seed(args.base_url, args.per_district, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
