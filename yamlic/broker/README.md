# Yamlic broker

Smista le richieste di Yamlic ai nodi che si collegano in uscita. Se non ci sono nodi liberi risponde il modello locale della VPS.
API compatibile OpenAI: i client (add-on Yamlic) usano l'indirizzo del broker come `llm_url` e il proprio token come `llm_key`.

Come funziona: il client chiama `/v1/chat/completions`; il broker mette il lavoro in coda; un nodo lo prende con `/node/poll`
(long polling, nessuna porta aperta sul router del nodo), conferma con `/node/ack`, lo esegue sul modello locale e restituisce
il risultato con `/node/result`. Se il nodo non conferma entro 8 secondi o fallisce, il lavoro passa a un altro nodo o alla VPS.

Privacy: richieste e risposte non vengono scritte nei log ne su disco; restano in memoria solo per la durata del lavoro.
Contano solo il numero di richieste per client e per nodo (`stats.json`). Il nodo che esegue il lavoro vede il testo della richiesta e
l'elenco delle entita: va detto agli utenti.

## Installazione sulla VPS
1. Copia `broker.py` sulla VPS (dal PC): `scp broker.py root@IP_VPS:/opt/yamlic/` (crea prima la cartella con `mkdir -p /opt/yamlic`).
2. Crea i token (uno per client, uno per nodo):
```bash
cd /opt/yamlic
C=$(python3 -c "import secrets;print(secrets.token_hex(16))"); N=$(python3 -c "import secrets;print(secrets.token_hex(16))")
echo "{\"clients\":{\"$C\":\"diego\"},\"nodes\":{\"$N\":\"vps-prova\"}}" > tokens.json && chmod 600 tokens.json
echo "token client: $C"; echo "token nodo: $N"
```
3. Servizio (usa la chiave del modello locale in FALLBACK_KEY):
```bash
cat > /etc/systemd/system/yamlic-broker.service <<'EOT'
[Unit]
Description=Yamlic broker
After=network.target
[Service]
WorkingDirectory=/opt/yamlic
Environment=BROKER_PORT=18090
Environment=FALLBACK_URL=http://127.0.0.1:18080
Environment=FALLBACK_KEY=LA_CHIAVE_DEL_MODELLO_LOCALE
ExecStart=/usr/bin/python3 /opt/yamlic/broker.py
Restart=always
User=nobody
[Install]
WantedBy=multi-user.target
EOT
chown nobody /opt/yamlic/tokens.json; systemctl daemon-reload && systemctl enable --now yamlic-broker
curl -s http://127.0.0.1:18090/health
```
4. nginx: nel file `/etc/nginx/conf.d/llm.conf` cambia la porta del `proxy_pass` da 18080 a 18090 (`sed -i 's/18080/18090/' /etc/nginx/conf.d/llm.conf`), poi `nginx -t && systemctl reload nginx`.
5. Nell'add-on Yamlic imposta `llm_key` con il token client.

## Variabili
`BROKER_PORT` (18090), `BROKER_TOKENS` (tokens.json), `FALLBACK_URL`, `FALLBACK_KEY`, `NODE_WAIT` (60 s), `ACK_WAIT` (8 s), `RATE_PER_MIN` (30 per client), `STATS_FILE`.

## Limiti attuali (prima versione)
- I token si creano a mano. Una registrazione automatica dei nodi viene dopo.
- Un nodo malevolo potrebbe restituire un YAML sbagliato: l'add-on deve validarlo (validatore severo) e in futuro lo stesso lavoro andra a due nodi con confronto.
- Nessun sistema di crediti: solo conteggi.
- Un solo broker (la tua VPS): va bene per la prova, non e ancora decentralizzato.
