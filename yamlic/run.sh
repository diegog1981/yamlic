#!/bin/bash
# Funziona in Home Assistant (opzioni da /data/options.json) e da solo (variabili YAMLIC_*).
set -eu
get() { python3 -c "import core;v=core.load_options().get('$1','');print(str(v).lower() if isinstance(v,bool) else v)"; }

LLM_URL=$(get llm_url)
NODO=$(get nodo)
USA_RETE=$(get usa_rete)
NEED_MODEL=0
if [ -z "$LLM_URL" ]; then
  if [ "$NODO" = "true" ] || [ "$NODO" = "1" ] || [ "$USA_RETE" = "false" ] || [ "$USA_RETE" = "0" ]; then NEED_MODEL=1; fi
fi

if [ "$NEED_MODEL" = "1" ]; then
  MODEL=$(get model); CTX=$(get context)
  [ -n "$CTX" ] || CTX=2048
  if [ "$MODEL" = "0.5b" ]; then
    FILE=qwen2.5-coder-0.5b-instruct-q4_k_m.gguf
    URL=https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/$FILE
  else
    FILE=qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
    URL=https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/$FILE
  fi
  mkdir -p /data/models
  if [ ! -s "/data/models/$FILE" ]; then
    echo "Scarico il modello $FILE ..."
    curl -fL --retry 3 -o "/data/models/$FILE.part" "$URL"
    mv "/data/models/$FILE.part" "/data/models/$FILE"
  fi
  THREADS=$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))
  nice -n 10 /app/llama-server -m "/data/models/$FILE" --host 127.0.0.1 --port 8080 -c "$CTX" -t "$THREADS" &
  until curl -sf http://127.0.0.1:8080/health >/dev/null; do sleep 2; done
  export YAMLIC_LOCAL_MODEL=1
  echo "Modello pronto"
fi

exec python3 /yamlic/server.py
