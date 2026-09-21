#!/usr/bin/env bash
set -euo pipefail

# Layer 1 smoke runner
# Usage:
#   BASE_URL=http://localhost:5000 WEBHOOK_SHARED_TOKEN=... WHATSAPP_VERIFY_TOKEN=... ADMIN_API_TOKEN=... ./scripts/layer1_smoke.sh

BASE_URL="${BASE_URL:-http://localhost:5000}"
WEBHOOK_SHARED_TOKEN="${WEBHOOK_SHARED_TOKEN:-visbharat-webhook-token}"
WHATSAPP_VERIFY_TOKEN="${WHATSAPP_VERIFY_TOKEN:-visbharat-whatsapp-verify}"
ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-visbharat-admin-token}"
DISTRICT="${DISTRICT:-}"

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t layer1smoke)"
trap 'rm -rf "$TMP_DIR"' EXIT

MATRIX_FILE="$TMP_DIR/matrix.tsv"
: > "$MATRIX_FILE"

pick_district() {
  if [[ -n "$DISTRICT" ]]; then
    echo "$DISTRICT"
    return
  fi

  local body_file="$TMP_DIR/districts.json"
  local code
  code=$(curl -sS -o "$body_file" -w "%{http_code}" "$BASE_URL/api/districts" || echo "000")
  if [[ "$code" == "200" ]]; then
    python - "$body_file" <<'PY'
import json,sys
path=sys.argv[1]
try:
    data=json.load(open(path,'r',encoding='utf-8'))
except Exception:
    print('')
    raise SystemExit(0)
items=data if isinstance(data,list) else data.get('districts',[])
if not items:
    print('')
elif isinstance(items[0],str):
    print(items[0])
else:
    print((items[0].get('district') or items[0].get('name') or '').strip())
PY
  else
    echo "Chennai"
  fi
}

DISTRICT="$(pick_district)"
[[ -z "$DISTRICT" ]] && DISTRICT="Chennai"

record_result() {
  local name="$1"
  local code="$2"
  local pass="$3"
  local note="$4"
  printf "%s\t%s\t%s\t%s\n" "$name" "$code" "$pass" "$note" >> "$MATRIX_FILE"
}

json_success_field() {
  local file="$1"
  python - "$file" <<'PY'
import json,sys
try:
    data=json.load(open(sys.argv[1],'r',encoding='utf-8'))
except Exception:
    print('')
    raise SystemExit(0)
if isinstance(data,dict) and 'success' in data:
    print(str(data.get('success')).lower())
else:
    print('')
PY
}

run_json_check() {
  local name="$1"
  local method="$2"
  local path="$3"
  local payload_file="$4"
  local auth_mode="$5" # webhook|admin|none

  local out="$TMP_DIR/${name}.json"
  local headers=(-H "Content-Type: application/json")
  if [[ "$auth_mode" == "webhook" ]]; then
    headers+=( -H "X-Webhook-Token: $WEBHOOK_SHARED_TOKEN" )
  elif [[ "$auth_mode" == "admin" ]]; then
    headers+=( -H "Authorization: Bearer $ADMIN_API_TOKEN" )
  fi

  local code
  code=$(curl -sS -o "$out" -w "%{http_code}" -X "$method" "${headers[@]}" --data-binary "@$payload_file" "$BASE_URL$path" || echo "000")
  local success_field
  success_field="$(json_success_field "$out")"
  local pass="FAIL"
  if [[ "$code" == "200" && "$success_field" != "false" ]]; then
    pass="PASS"
  fi
  record_result "$name" "$code" "$pass" "success=$success_field"
}

# 1) Web submit
cat > "$TMP_DIR/web_submit.json" <<EOF
{"text":"Layer1 smoke web submit","language":"en","district":"$DISTRICT","source":"Web"}
EOF
run_json_check "web_submit" "POST" "/api/submit" "$TMP_DIR/web_submit.json" "none"

# 2) Voice submit
cat > "$TMP_DIR/voice_submit.json" <<EOF
{"language":"ta","district":"$DISTRICT","source":"Voice IVR","audio_base64":"dGVzdA==","audio_mime_type":"audio/webm;codecs=opus"}
EOF
run_json_check "voice_submit" "POST" "/api/submit-voice" "$TMP_DIR/voice_submit.json" "none"

# 3) WhatsApp verification GET
challenge="layer1_challenge"
wa_body="$TMP_DIR/wa_verify.txt"
wa_code=$(curl -sS -o "$wa_body" -w "%{http_code}" "$BASE_URL/api/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=$WHATSAPP_VERIFY_TOKEN&hub.challenge=$challenge" || echo "000")
wa_text="$(cat "$wa_body" 2>/dev/null || true)"
if [[ "$wa_code" == "200" && "$wa_text" == "$challenge" ]]; then
  record_result "whatsapp_verify_get" "$wa_code" "PASS" "challenge_echo=true"
else
  record_result "whatsapp_verify_get" "$wa_code" "FAIL" "challenge_echo=false"
fi

# 4) WhatsApp inbound webhook
cat > "$TMP_DIR/wa_inbound.json" <<EOF
{"message":"Layer1 smoke whatsapp","language":"en","district":"$DISTRICT","from":"919900000001"}
EOF
run_json_check "whatsapp_inbound_post" "POST" "/api/channels/whatsapp/webhook" "$TMP_DIR/wa_inbound.json" "webhook"

# 5) SMS keyword
cat > "$TMP_DIR/sms_keyword.json" <<EOF
{"text":"NV NEW Layer1 smoke keyword","from":"919900000002","district":"$DISTRICT"}
EOF
run_json_check "sms_keyword_new" "POST" "/api/channels/sms/keyword" "$TMP_DIR/sms_keyword.json" "webhook"

# 6) IVR missed call
cat > "$TMP_DIR/ivr_missed_call.json" <<EOF
{"phone":"919900000003","district":"$DISTRICT","language":"en"}
EOF
run_json_check "ivr_missed_call" "POST" "/api/channels/ivr/missed-call" "$TMP_DIR/ivr_missed_call.json" "webhook"

# 7) Open API federation
cat > "$TMP_DIR/fed_publish.json" <<EOF
{"source":"layer1-smoke","external_event_id":"smoke-$(date +%s)","district":"$DISTRICT","text":"Layer1 smoke federation","language":"en","channel":"Open API"}
EOF
run_json_check "open_api_federation_publish" "POST" "/api/v1/federation/publish" "$TMP_DIR/fed_publish.json" "admin"

echo
printf "%-34s %-8s %-6s %s\n" "CHECK" "HTTP" "RESULT" "NOTE"
printf "%-34s %-8s %-6s %s\n" "----------------------------------" "--------" "------" "------------------------------"

pass_count=0
total_count=0
while IFS=$'\t' read -r name code result note; do
  [[ -z "$name" ]] && continue
  printf "%-34s %-8s %-6s %s\n" "$name" "$code" "$result" "$note"
  total_count=$((total_count + 1))
  [[ "$result" == "PASS" ]] && pass_count=$((pass_count + 1))
done < "$MATRIX_FILE"

echo
echo "Layer1 Smoke Summary: $pass_count/$total_count passed"
if [[ "$pass_count" -ne "$total_count" ]]; then
  exit 1
fi

