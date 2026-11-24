#!/bin/bash
set -eu
OPT=/data/options.json
get() { python3 -c "import json;print(json.load(open('$OPT')).get('$1',''))"; }
FILE=qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
URL=https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/$FILE
THREADS=$(get threads)
[ -z "$THREADS" ] || [ "$THREADS" = "0" ] && THREADS=$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))

mkdir -p /data/models
if [ ! -s "/data/models/$FILE" ]; then
  echo "Scarico il modello $FILE ..."
  curl -fL --retry 3 -o "/data/models/$FILE.part" "$URL"
  mv "/data/models/$FILE.part" "/data/models/$FILE"
fi

nice -n 10 /app/llama-server -m "/data/models/$FILE" --host 127.0.0.1 --port 8081 -c 2048 -t "$THREADS" &
until curl -sf http://127.0.0.1:8081/health >/dev/null; do sleep 2; done
echo "Modello pronto, mi collego alla rete"
exec python3 /yamlic_nodo/agent.py
