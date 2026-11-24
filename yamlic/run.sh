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

# scarica $2 in $1 (con ripresa e controllo sha256 $3, se dato)
scarica() {
  local dest="$1" url="$2" sha="${3:-}"
  curl -fL --retry 3 -C - -o "$dest.part" "$url" || { rm -f "$dest.part"; return 1; }
  if [ -n "$sha" ] && ! echo "$sha  $dest.part" | sha256sum -c - >/dev/null 2>&1; then
    echo "Controllo sha256 fallito per $(basename "$dest")"; rm -f "$dest.part"; return 1
  fi
  mv "$dest.part" "$dest"
}

if [ "$NEED_MODEL" = "1" ]; then
  MODEL=$(get model); CTX=$(get context)
  [ -n "$CTX" ] || CTX=2048
  SHA=""; BASE_FILE=""; BASE_URL=""
  if [ "$MODEL" = "0.5b" ]; then
    FILE=qwen2.5-coder-0.5b-instruct-q4_k_m.gguf
    URL=https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/$FILE
  else
    # modello addestrato per Yamlic (v2); se non e raggiungibile si usa il modello base
    FILE=yamlic-v2.gguf
    URL=https://llm.narriq.app/modello/yamlic-v2.gguf
    SHA=e85bcaba6549bbed049cbdae9a71aab378327e08ef9c64faf6a899209aec7402
    BASE_FILE=qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
    BASE_URL=https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/$BASE_FILE
  fi
  mkdir -p /data/models
  if [ ! -s "/data/models/$FILE" ]; then
    echo "Scarico il modello $FILE ..."
    if ! scarica "/data/models/$FILE" "$URL" "$SHA"; then
      if [ -n "$BASE_FILE" ]; then
        echo "Modello Yamlic non raggiungibile: uso il modello base $BASE_FILE"
        FILE=$BASE_FILE; URL=$BASE_URL
        [ -s "/data/models/$FILE" ] || scarica "/data/models/$FILE" "$URL" ""
      else
        echo "Download del modello fallito"; exit 1
      fi
    fi
  fi
  THREADS=$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))
  nice -n 10 /app/llama-server -m "/data/models/$FILE" --host 127.0.0.1 --port 8080 -c "$CTX" -t "$THREADS" &
  until curl -sf http://127.0.0.1:8080/health >/dev/null; do sleep 2; done
  export YAMLIC_LOCAL_MODEL=1
  echo "Modello pronto"
fi

exec python3 /yamlic/server.py
