# Yamlic

**Yaml Intelligent Creator**: scrivi cosa vuoi in italiano, Yamlic scrive l'automazione per Home Assistant con le entità vere di casa tua.

> **Versione alpha, solo per test.** Non tutte le automazioni funzionano: alcune frasi non le capisce, altre le capisce male. Controlla sempre il riassunto e lo YAML prima di usarli. Progetto indipendente, non ufficiale e non affiliato a Home Assistant o Nabu Casa.

## Come funziona
1. Scrivi la frase come la diresti a voce, per esempio "al tramonto chiudi le tende del salotto".
2. Yamlic la divide in pezzi (quando, condizioni, cosa fare) e gran parte la capisce da solo con regole che girano in casa tua.
3. I dispositivi li sceglie sempre Yamlic, leggendo le tue entità, le stanze e quello che gli hai detto in "La tua casa". Se due dispositivi sono simili ti chiede quale intendi.
4. Solo i pezzi che le regole non capiscono vanno a un piccolo modello AI. Quello che risponde viene ricontrollato: dispositivi che non c'entrano e comandi inesistenti vengono scartati; serrature e allarmi solo se li nomini tu.
5. Se qualcosa non è chiaro ti fa una domanda con i pulsanti.
6. Ti mostra un riassunto in italiano e lo YAML. Sa fare anche scene, notifiche sul telefono di una persona e annunci sugli assistenti vocali.

Non salva nulla da solo (opzione `allow_create` spenta).

## Installazione
[![Aggiungi il repository a Home Assistant](https://my.home-assistant.io/badges/supervisor_add_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdiegog1981%2Fyamlic)

1. Clicca il pulsante qui sopra (oppure Impostazioni > App/Add-on > Store > menu in alto a destra > Repository e incolla `https://github.com/diegog1981/yamlic`).
2. Installa "Yamlic" e avvialo.
3. Apri `http://<ip-di-home-assistant>:8099`.
4. Apri "La tua casa" e rispondi alle domande; nelle opzioni imposta la tua persona (`io_person`).

## Rete o tutto in casa
- **Di base (rete)**: non scarica nessun modello. I pezzi che le regole non capiscono vanno a un nodo della rete con nomi, entità e testi sostituiti da codici: il nodo non vede la tua casa. Il contenuto è cifrato dal tuo Home Assistant al nodo.
- **Tutto in casa**: metti "Dove far girare il modello" su `locale` e riavvia. Al primo avvio scarica il modello (circa 1 GB) e da lì non esce niente. Servono almeno 4 core e 4 GB di RAM liberi; è più lento della rete.
- **Tuo server del modello**: imposta `llm_url` (e `llm_key`) per usare un tuo server llama.cpp.

## Nodo (facoltativo)
Con "Metti a disposizione il tuo server" acceso, il tuo server risponde alle richieste degli altri utenti con il modello: serve solo a distribuire la potenza di calcolo. Non addestra niente, non prende dati da casa tua, vede solo codici e lavora solo quando la macchina è libera. Al primo avvio scarica il modello (circa 1 GB); servono almeno 4 core e 4 GB di RAM liberi.

## Segnalazioni e addestramento
Quando un risultato è sbagliato puoi premere "Invia a Diego": parte solo anonimo (codici al posto dei nomi), solo se lo premi e dopo averti mostrato cosa invia.
Non c'è addestramento automatico, né sui server degli utenti: le segnalazioni vengono controllate a mano, quelle scelte diventano esempi nuovi, il modello viene riaddestrato su un server separato e poi i nodi scaricano la versione nuova.

## Opzioni principali
- `modo`: `auto`, `locale` (tutto in casa) o `rete`.
- `nodo`: metti a disposizione il tuo server (spento di default).
- `allow_create`: se acceso compare il pulsante per salvare in Home Assistant.
- `io_person`: la tua entità persona, così "quando esco" si riferisce a te.
- `model`: `0.5b` o `1.5b` (solo se il modello viene scaricato).

## Licenza
Codice: MIT. Modello di base: Qwen2.5-Coder (licenza sulla pagina del modello su Hugging Face).
