# Yamlic

**Yaml Intelligent Creator**: scrivi cosa vuoi in italiano, Yamlic scrive l'automazione per Home Assistant.

Funziona in locale: un modello piccolo (Qwen2.5-Coder 1,5B) gira sul tuo server oppure su un nodo della rete. Non salva nulla da solo: mostra l'YAML, controlla che le entita esistano e ti lascia decidere.

> Progetto indipendente, non ufficiale e non affiliato a Home Assistant o Nabu Casa. Versione di prova: aspettati errori.

## Installazione
[![Aggiungi il repository a Home Assistant](https://my.home-assistant.io/badges/supervisor_add_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdiegog1981%2Fyamlic)

1. Clicca il pulsante qui sopra (oppure Impostazioni > App/Add-on > Store > menu in alto a destra > Repository e incolla `https://github.com/diegog1981/yamlic`).
2. Installa "Yamlic" e avvialo. Al primo avvio scarica il modello (circa 1 GB).
3. Apri `http://<ip-di-home-assistant>:8099`.

## Hardware
Servono almeno 4 core e 4 GB di RAM liberi per far girare il modello in casa. Con meno (Raspberry, mini PC lenti) lascia `usa_rete` attivo: la risposta arriva da un nodo veloce. In alternativa imposta `llm_url` (e `llm_key`) per un tuo server.

## Opzioni
- `model`: `0.5b` o `1.5b`.
- `context`: lunghezza del contesto (1024-8192).
- `allow_create`: se `true` puo salvare l'automazione. Lascia `false` per le prime prove.
- `llm_url`, `llm_key`: server llama.cpp esterno (al posto del modello locale).
- `io_person`: la tua entita person, se vuoi che "io" si riferisca a una persona precisa.

## Rete (opzionale)
Ogni installazione puo avere tre ruoli, tutti facoltativi:
- **cliente**: se il tuo server e lento, chiede la risposta alla rete (`usa_rete`). Prima di uscire, nomi, entita e testi vengono sostituiti da alias (`light.e1`, `@A1`) e rimessi al ritorno: la rete non vede la tua casa.
- **nodo**: mette a disposizione la potenza libera, solo quando la macchina e ferma (`nodo`, `solo_se_libero`).
- **smistatore**: mette in contatto clienti e nodi; serve una porta raggiungibile, quindi di solito una VPS (`smistatore`). Vedi `smistatore/README.md`.

Tutto si collega in uscita: niente porte da aprire. Il contenuto e cifrato dal cliente al nodo, e ogni risposta della rete passa dal validatore prima di mostrarti l'YAML (azioni su serrature e allarmi bloccate se non le hai chieste).

## Licenza
Codice: da definire (proposta MIT). Modello: Qwen2.5-Coder 1,5B, vedi la pagina del modello su Hugging Face.
