# Med & Tech — Daily Check Campagne

Snapshot statici giornalieri sulle campagne Meta Lead Ads (Total Lift / Total Sculpt) del cliente **Med & Tech**, generati e pubblicati automaticamente da Francesco Maria Mosca.

**Online:** https://advfmosca.github.io/med-tech-daily-check/
**Generazione:** scheduled task `med-tech-daily-total-lift-sculpt` ogni giorno alle ~15:00.
**Fonte dati:** Windsor.ai → Meta Ads BM "B&M Srl" → account `533672775128363`.

## Struttura

- `index.html` — landing con elenco di tutti i giorni
- `med-tech-daily-YYYY-MM-DD.html` — un report per giorno (self-contained, dati baked-in)
- `_data/data-YYYY-MM-DD.json` — KPI pre-aggregati (utile per backfill o ricostruzione index)
- `_template/med-tech-daily-template.html` — template HTML del singolo report
- `_scripts/run_daily_pipeline.py` — generatore end-to-end

## Pipeline giornaliera (automatica)

1. Claude fetcha Windsor.ai daily + hourly → due file JSON
2. `run_daily_pipeline.py` orchestra tutto:
   - filtro spending > 0 (oggi & ieri)
   - calcolo KPI (CPL, trend orario, freq 7gg) + semaforo per ogni campagna
   - render HTML del giorno
   - aggiornamento `index.html`
   - `git push` → GitHub Pages aggiorna
3. Claude posta su Slack `#cea-medandtech-b2c` il link cliccabile

## Rigenera manualmente un giorno

```bash
python3 _scripts/run_daily_pipeline.py \
  --daily-json /path/to/daily.json \
  --hourly-json /path/to/hourly.json \
  --oggi 2026-05-15 \
  --cutoff-hour 15 \
  --repo-root . \
  --push
```

## Semafori

- **VERDE** — CPL in linea o sotto la media storica della campagna
- **GIALLO** — CPL fino a +50% sopra media
- **ROSSO** — 0 lead nonostante spend, o CPL oltre +50% sopra media
- **NERO** — nessuna spesa registrata ieri (delivery ferma)

Logica completa e regole "Cosa faremo per migliorare le performance" in `_scripts/run_daily_pipeline.py`.
