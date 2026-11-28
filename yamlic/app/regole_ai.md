# Regole extra per il modello (si aggiungono in coda al prompt di base)
# Una regola per riga. Le righe che iniziano con # sono ignorate. Massimo circa 800 caratteri in totale.
- Non inventare mai entity_id: se una entita non e nell'elenco, non usarla.
- Per le notifiche usa solo un servizio notify.* presente nell'elenco, altrimenti notify.notify.
- Il trigger e l'evento detto nella frase (sensore, porta, orario), mai l'entita che l'azione comanda.
- Mantieni tutte le condizioni della frase (allarme, presenza, orario, sensore).
- Per le luci usa light.turn_on con brightness_pct, rgb_color o color_temp_kelvin, mai "temperature".
- Per condizionatore e termostato usa climate.*, mai switch.
