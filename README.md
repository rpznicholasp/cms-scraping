# octavian-scraping

Script per scaricare le CMS pages dall'API Octavian (`/accounting-service/v3/admin/api/cmsPages/{id}`),
salvarle su file system e su SQLite.

## Cosa fa

- Cicla un range di id (`start`..`end`), con delay configurabile tra una richiesta e l'altra.
- Per ogni id trovato (status 200, dati non vuoti):
  - salva il body come file in `output/idLicensee/idSkin/version/<url>`, con estensione presa da `url` (`.xxx` → `.scss`, nessuna estensione → `.html`).
  - in caso di omonimia col file già presente, rinomina il vecchio file in `nomefile_lastUpdate.estensione` prima di scrivere il nuovo.
  - salva il record completo (JSON incluso) nella tabella `cms_pages` del db SQLite.
- Gli id che rispondono 404 o con dati vuoti vengono ignorati.
- Gli id che falliscono per errore (eccezione di rete o status diverso da 200/404) vengono salvati nella tabella `failed_ids`.
- Durante l'esecuzione mostra una progress bar (rich) con percentuale, conteggio, tempo trascorso/rimanente e ultimo esito.
- Interrompibile in sicurezza con Ctrl+C: l'iterazione in corso finisce di scrivere, poi stampa il report parziale.
- A fine esecuzione stampa un report: id processati, file scaricati, non trovati, errori, tempo totale.

## Requisiti

- Python >= 3.9
- [uv](https://docs.astral.sh/uv/) (gestisce le dipendenze in automatico tramite l'header PEP 723 dentro `scrape.py`)

## Configurazione

Modifica `config.json`:

```json
{
  "token": "il tuo token, senza il prefisso 'token '",
  "base_url": "https://live.octavianlab.com/accounting-service/v3/admin/api/cmsPages",
  "start": 0,
  "end": 15000,
  "delay": 0.5,
  "db_path": "cmspages.db",
  "output_dir": "output"
}
```

- `start` / `end`: range di id da ciclare (end escluso).
- `delay`: secondi di pausa tra una richiesta e l'altra.
- `db_path`: file SQLite di output.
- `output_dir`: cartella radice dove salvare i file scaricati.

## Esecuzione

Scarica tutto il range configurato:

```bash
uv run scrape.py --sync
# oppure, senza flag: il default è sync
uv run scrape.py
```

Riprova solo gli id falliti in precedenza (letti da `failed_ids`); quelli che vanno a buon fine vengono rimossi dalla tabella:

```bash
uv run scrape.py --retry
```

Alias corti disponibili: `-s` per `--sync`, `-r` per `--retry`.

Help:

```bash
uv run scrape.py -h
```

## Database

- `cms_pages`: un record per id scaricato con successo (tutti i campi della risposta API + `raw_json`).
- `failed_ids`: id che hanno dato errore, con `reason` e `last_attempt`; svuotata via `--retry` quando l'id va a buon fine.
