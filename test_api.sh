#!/usr/bin/env bash
# Uji end-to-end API sync di server. Semua assertion harus lulus.
set -u
B=http://127.0.0.1:18000
K=testkey123
PASS=0; FAIL=0
ck(){ if [ "$2" = "$3" ]; then echo "  PASS  $1"; PASS=$((PASS+1));
      else echo "  FAIL  $1 (dapat '$2', harusnya '$3')"; FAIL=$((FAIL+1)); fi; }

echo "=== 1. AUTH ==="
code=$(curl -s -o /dev/null -w '%{http_code}' $B/pull?device_id=t1)
ck "tanpa key -> 401" "$code" "401"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Sync-Key: salah" $B/pull?device_id=t1)
ck "key salah -> 401" "$code" "401"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Sync-Key: $K" $B/pull?device_id=t1)
ck "key benar -> 200" "$code" "200"

echo "=== 2. PUSH dari PC-A ==="
r=$(curl -s -X POST $B/push -H "X-Sync-Key: $K" -H 'Content-Type: application/json' -d '{
 "device_id":"PC-A","device_name":"Laptop Bos","platform":"win32",
 "records":[{"kind":"sprint","key":"1","updated_at":"2026-09-18T10:00:00Z",
   "payload":{"id":1,"label":"#1","tasks":[{"text":"Design logo","est":25,"status":"done"}]}}]}')
echo "  $r"
echo "$r" | grep -q '"applied":1' && { echo "  PASS  push 1 record"; PASS=$((PASS+1)); } || { echo "  FAIL  push"; FAIL=$((FAIL+1)); }

echo "=== 3. PULL dari PC-B (device beda) ==="
r=$(curl -s -H "X-Sync-Key: $K" "$B/pull?device_id=PC-B")
echo "$r" | grep -q '"count":1' && { echo "  PASS  PC-B terima 1 record"; PASS=$((PASS+1)); } || { echo "  FAIL  pull: $r"; FAIL=$((FAIL+1)); }
echo "$r" | grep -q 'Design logo' && { echo "  PASS  payload utuh"; PASS=$((PASS+1)); } || { echo "  FAIL  payload"; FAIL=$((FAIL+1)); }

echo "=== 4. PC-B tidak menerima record miliknya sendiri ==="
curl -s -X POST $B/push -H "X-Sync-Key: $K" -H 'Content-Type: application/json' -d '{
 "device_id":"PC-B","records":[{"kind":"idea","key":"9","updated_at":"2026-09-18T10:05:00Z","payload":{"t":"ide B"}}]}' >/dev/null
r=$(curl -s -H "X-Sync-Key: $K" "$B/pull?device_id=PC-B")
echo "$r" | grep -q 'ide B' && { echo "  FAIL  PC-B terima record sendiri"; FAIL=$((FAIL+1)); } || { echo "  PASS  record sendiri diabaikan"; PASS=$((PASS+1)); }

echo "=== 5. LAST-WRITE-WINS ==="
# PC-A update sprint 1 dengan waktu LEBIH LAMA -> harus ditolak
r=$(curl -s -X POST $B/push -H "X-Sync-Key: $K" -H 'Content-Type: application/json' -d '{
 "device_id":"PC-A","records":[{"kind":"sprint","key":"1","updated_at":"2026-09-18T09:00:00Z",
   "payload":{"id":1,"label":"LAMA"}}]}')
echo "  $r"
echo "$r" | grep -q '"skipped":1' && { echo "  PASS  record lama ditolak"; PASS=$((PASS+1)); } || { echo "  FAIL  LW baru seharusnya ditolak"; FAIL=$((FAIL+1)); }
r=$(curl -s -H "X-Sync-Key: $K" "$B/pull?device_id=PC-B")
echo "$r" | grep -q 'Design logo' && { echo "  PASS  data lama tidak menimpa"; PASS=$((PASS+1)); } || { echo "  FAIL  data ketimpa!"; FAIL=$((FAIL+1)); }
echo "$r" | grep -q 'LAMA' && { echo "  FAIL  payload lama masuk"; FAIL=$((FAIL+1)); } || { echo "  PASS  payload lama tidak masuk"; PASS=$((PASS+1)); }

# PC-A update dengan waktu LEBIH BARU -> harus diterima
r=$(curl -s -X POST $B/push -H "X-Sync-Key: $K" -H 'Content-Type: application/json' -d '{
 "device_id":"PC-A","records":[{"kind":"sprint","key":"1","updated_at":"2026-09-18T11:00:00Z",
   "payload":{"id":1,"label":"BARU"}}]}')
echo "$r" | grep -q '"applied":1' && { echo "  PASS  record baru diterima"; PASS=$((PASS+1)); } || { echo "  FAIL  record baru ditolak"; FAIL=$((FAIL+1)); }

echo "=== 6. INCREMENTAL (since_seq) ==="
SEQ=$(curl -s -H "X-Sync-Key: $K" "$B/pull?device_id=PC-C" | python3 -c 'import sys,json;print(json.load(sys.stdin)["server_seq"])')
echo "  server_seq sekarang: $SEQ"
r=$(curl -s -H "X-Sync-Key: $K" "$B/pull?device_id=PC-C&since_seq=$SEQ")
echo "$r" | grep -q '"count":0' && { echo "  PASS  since_seq terbaru -> 0 record"; PASS=$((PASS+1)); } || { echo "  FAIL  incremental: $r"; FAIL=$((FAIL+1)); }

echo "=== 7. VALIDASI ==="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST $B/push -H "X-Sync-Key: $K" -H 'Content-Type: application/json' -d '{"device_id":"PC-A","records":[{"kind":"ngawur","key":"1","updated_at":"2026-09-18T10:00:00Z","payload":{}}]}')
ck "kind tidak dikenal -> 400" "$code" "400"

echo "=== 8. DEVICES & STATS ==="
echo "  devices: $(curl -s -H "X-Sync-Key: $K" $B/devices | head -c 300)"
echo "  stats:   $(curl -s -H "X-Sync-Key: $K" $B/stats)"

echo
echo "======== HASIL: $PASS lulus, $FAIL gagal ========"
[ "$FAIL" = "0" ] && echo "SEMUA UJI LULUS" || echo "ADA YANG GAGAL"