# Yamlic: come funziona

Yamlic (Yaml Intelligent Creator) e un add-on di Home Assistant: scrivi in italiano cosa vuoi e scrive l'automazione YAML usando le entita vere di casa tua. Progetto indipendente, non ufficiale e non affiliato a Home Assistant o Nabu Casa. Versione di prova: l'YAML va sempre controllato prima di usarlo.

## Cosa succede quando scrivi una frase
1. L'add-on legge entita e aree di Home Assistant.
2. Se la frase e ambigua (per esempio "tutte le luci" con interruttori simili a luci) fa una domanda. Sono regole fisse, non il modello.
3. Sceglie le entita rilevanti (stanze con sinonimi, esclude indicatori, schermi, cancelli e simili) e le passa a un piccolo modello (Qwen2.5-Coder-1.5B con LoRA).
4. Corregge la bozza con regole fisse: entita o persone inventate, orari, giorni, alba e tramonto, presenza ("quando esco", "nessuno in casa"), condizioni inventate, cancelli a impulso.
5. Un validatore controlla formato, servizi, entita esistenti e stati. Le azioni su serrature e allarmi sono bloccate se non le hai chieste.
6. Ti mostra l'YAML con gli avvisi. Non salva nulla da solo: `allow_create` e spento di default.

## Modello e rete
- Il modello puo girare in casa (opzionale) o sui nodi della rete (default). Ogni installazione puo essere cliente, nodo e smistatore: tre ruoli facoltativi.
- Solo connessioni in uscita (WebSocket su 443): niente porte da aprire.
- Prima di uscire da casa nomi, entita e testi tra virgolette sono sostituiti da codici (`light.e1`, `_A1`, `$T1`) e rimessi a posto al ritorno. La richiesta e cifrata dal cliente al nodo; lo smistatore non la legge.
- Le risposte dalla rete passano dal validatore; la richiesta va a due nodi e vale la prima risposta valida.

## Segnalare un errore
- **Copia per segnalare** copia richiesta, YAML e avvisi (a mano, con i tuoi nomi).
- **Invia a Diego** mostra prima esattamente cosa parte e solo dopo il tuo clic lo invia. E anonimo: nomi ed entita sono gia codici. Niente parte in automatico.

## Limiti noti
Il modello sbaglia ancora su: testi di notifica inventati, trigger da pulsante, scene con piu azioni, eccezioni con "tranne", dispositivi che non esistono in casa. "99% validi" misura la validita dell'YAML, non se fa cio che volevi.

## Per sviluppatori
Codice in `yamlic/app/` (`core.py` selezione e regole, `server.py` pagina e API, `rete.py` rete). Si pubblica aumentando `version` in `yamlic/config.yaml`: GitHub Actions costruisce l'immagine `ghcr.io/diegog1981/yamlic:<versione>`.
