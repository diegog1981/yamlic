# Smistatore e nodo su un server (VPS o PC)

Ogni installazione di Yamlic e uguale: la stessa app puo fare da cliente, nodo e smistatore. La tua VPS e un nodo come gli altri, con in piu il ruolo smistatore perche ha una porta raggiungibile. Gli smistatori si possono moltiplicare: chi ha una VPS accende il ruolo e lo aggiunge alla lista `smistatori`.

Lo smistatore non legge le richieste (sono cifrate dal cliente verso il nodo) e non registra niente.

## Installazione senza Docker (nessun rischio per quello che gira gia)
```bash
sudo mkdir -p /opt/yamlic && sudo chown $USER /opt/yamlic
cd /opt/yamlic
# copia qui la cartella yamlic/app del repository (core.py rete.py direct.py server.py)
python3 -m venv venv && ./venv/bin/pip install pyyaml aiohttp cryptography aiortc
```
Servizio `/etc/systemd/system/yamlic.service`:
```ini
[Unit]
Description=Yamlic nodo + smistatore
After=network-online.target

[Service]
WorkingDirectory=/opt/yamlic/app
Environment=PYTHONPATH=/opt/yamlic/app
Environment=YAMLIC_IDENTITY=/opt/yamlic/identity.json
Environment=YAMLIC_NODO=1
Environment=YAMLIC_SMISTATORE=1
Environment=YAMLIC_PORTA_SMISTATORE=8100
Environment=YAMLIC_SOLO_SE_LIBERO=0
Environment=YAMLIC_LLM_URL=http://127.0.0.1:18080
Environment=YAMLIC_LLM_KEY=LA_TUA_CHIAVE_DEL_MODELLO
Environment=YAMLIC_SMISTATORI=wss://llm.narriq.app/ws
ExecStart=/opt/yamlic/venv/bin/python server.py
Restart=always
User=TUO_UTENTE

[Install]
WantedBy=multi-user.target
```
`sudo systemctl daemon-reload && sudo systemctl enable --now yamlic && journalctl -u yamlic -n 20`
Deve comparire "Smistatore attivo" e "Nodo attivo, velocita N token/s".

## Nginx (solo aggiunta, con backup)
In `/etc/nginx/conf.d/llm.conf`, dentro il blocco `server` HTTPS, prima di `location /`:
```nginx
location = /ws {
    proxy_pass http://127.0.0.1:8100;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```
`sudo cp /etc/nginx/conf.d/llm.conf /etc/nginx/conf.d/llm.conf.bak && sudo nginx -t && sudo systemctl reload nginx`

## Con Docker
Dopo la pubblicazione dell'immagine: `docker run -d --restart=always --network host -v yamlic:/data -e YAMLIC_NODO=1 -e YAMLIC_SMISTATORE=1 -e YAMLIC_LLM_URL=http://127.0.0.1:18080 -e YAMLIC_LLM_KEY=... ghcr.io/diegog1981/yamlic`

## Opzioni (variabili `YAMLIC_*` o opzioni dell'add-on)
`nodo`, `smistatore`, `usa_rete`, `smistatori` (separati da virgola), `altri_smistatori`, `porta_smistatore` (8100), `limite_cliente_min` (12 richieste al minuto per cliente), `solo_se_libero`, `carico_max`, `diretto` (WebRTC, sperimentale), `modo` (auto, locale, rete), `llm_url`, `llm_key`.

## Sicurezza in breve
- Identita = chiave Ed25519 locale; contenuto cifrato end to end (X25519 + ChaCha20-Poly1305).
- Il cliente invia solo alias (`light.e1`, `@A1`): nomi, id e testi restano in casa.
- Le risposte della rete passano dal validatore severo; azioni su serrature e allarmi bloccate se non richieste.
- Il nodo accetta solo richieste di chat piccole (max 600 token) e non esegue mai codice ricevuto.
