#!/usr/bin/env python3
"""
run_daily_pipeline.py

Pipeline end-to-end per Med & Tech Daily Check.

USAGE:
    python3 run_daily_pipeline.py \
        --daily-json /path/to/daily.json \
        --hourly-json /path/to/hourly.json \
        --oggi 2026-05-15 \
        --cutoff-hour 15 \
        --repo-root /path/to/local/repo \
        [--push]                       # se presente, fa anche git add+commit+push

OUTPUT su stdout (solo l'essenziale, formato chiave=valore per token-saving):
    N_CAMPAGNE=11
    ROSSO=5
    GIALLO=2
    VERDE=4
    NERO=0
    FILENAME=med-tech-daily-2026-05-15.html
    URL=https://advfmosca.github.io/med-tech-daily-check/med-tech-daily-2026-05-15.html
    COMMIT=<sha>      (se --push)
"""
import argparse, json, re, html, os, subprocess, sys
from collections import defaultdict
from datetime import datetime, timedelta, date

# ============================================================
# Costanti pipeline
# ============================================================
REPO_OWNER = "advfmosca"
REPO_NAME = "med-tech-daily-check"
PAGES_BASE_URL = f"https://{REPO_OWNER}.github.io/{REPO_NAME}"

CUTOFF_LABEL_FMT = "{:02d}:59"
LABEL = {"rosso": "ROSSO", "giallo": "GIALLO", "verde": "VERDE", "nero": "NERO"}
SEM_ORDER = {"rosso": 0, "nero": 1, "giallo": 2, "verde": 3}

# ============================================================
# Helpers
# ============================================================

def to_float(v):
    if v is None: return 0.0
    try: return float(v)
    except: return 0.0

def to_int(v):
    if v is None: return 0
    try: return int(v)
    except: return 0

def fmt_eur(x):
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_num_it(x, decimals=2):
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_pct(x, decimals=1):
    return f"{x:+.{decimals}f}".replace(".", ",") + "%"

def normalize_iso(s):
    if not s: return s
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return s

def esc(s):
    return html.escape(s if s is not None else "")

def giorni_text(g):
    if g is None: return "termine non definito"
    if g > 0: return f"{g}gg alla fine"
    if g == 0: return "termina oggi"
    return f"scaduta da {abs(g)}gg"

# ============================================================
# KPI computation
# ============================================================

def compute_cards(daily_records, hourly_records, oggi: date, cutoff_hour: int):
    ieri = oggi - timedelta(days=1)
    ieri1 = oggi - timedelta(days=2)
    oggi_s = oggi.isoformat()
    ieri_s = ieri.isoformat()
    ieri1_s = ieri1.isoformat()

    active = [r for r in daily_records
              if r.get("campaign_effective_status") == "ACTIVE"
              and r.get("adset_effective_status") == "ACTIVE"]

    by_cd = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "leads": 0,
                                 "freq_w": 0.0, "imp_w": 0})
    camp_starts = defaultdict(list)
    camp_ends = defaultdict(list)

    for r in active:
        c, d = r["campaign"], r["date"]
        spend = to_float(r.get("spend"))
        imp = to_int(r.get("impressions"))
        leads = to_int(r.get("actions_onsite_conversion_lead_grouped"))
        freq = to_float(r.get("frequency"))
        rec = by_cd[(c, d)]
        rec["spend"] += spend
        rec["impressions"] += imp
        rec["leads"] += leads
        if imp > 0 and freq:
            rec["freq_w"] += freq * imp
            rec["imp_w"] += imp
        if r.get("adset_start_time"): camp_starts[c].append(r["adset_start_time"])
        if r.get("adset_end_time"): camp_ends[c].append(r["adset_end_time"])

    # Spending filter
    all_camps = sorted({c for (c, d) in by_cd.keys()})
    kept = [c for c in all_camps
            if by_cd.get((c, oggi_s), {}).get("spend", 0.0) > 0
            and by_cd.get((c, ieri_s), {}).get("spend", 0.0) > 0]

    # Hourly aggregation
    hourly_by_cd = defaultdict(lambda: defaultdict(int))
    for r in hourly_records:
        c, d = r["campaign"], r["date"]
        leads = to_int(r.get("actions_onsite_conversion_lead_grouped"))
        slot = r.get("hourly_stats_aggregated_by_advertiser_time_zone", "")
        m = re.match(r"(\d+):", slot)
        if m:
            sh = int(m.group(1))
            hourly_by_cd[(c, d)][sh] += leads

    def get_so_far(c, d):
        h = hourly_by_cd.get((c, d), {})
        return sum(v for sh, v in h.items() if sh <= cutoff_hour)

    last7 = [(oggi - timedelta(days=i)).isoformat() for i in range(7)]

    cards = []
    for c in kept:
        total_spend = sum(by_cd[(c, d)]["spend"] for (cc, d) in by_cd if cc == c)
        total_leads = sum(by_cd[(c, d)]["leads"] for (cc, d) in by_cd if cc == c)
        cpl_media = (total_spend / total_leads) if total_leads > 0 else None

        spend_ieri = by_cd.get((c, ieri_s), {}).get("spend", 0.0)
        leads_ieri = by_cd.get((c, ieri_s), {}).get("leads", 0)
        cpl_ieri = (spend_ieri / leads_ieri) if leads_ieri > 0 else (float("inf") if spend_ieri > 0 else None)

        lead_oggi = by_cd.get((c, oggi_s), {}).get("leads", 0)
        lead_ieri1 = by_cd.get((c, ieri1_s), {}).get("leads", 0)
        trend_3g = (lead_oggi + leads_ieri + lead_ieri1) / 3.0

        leads_oggi_so_far = get_so_far(c, oggi_s)
        leads_ieri_so_far = get_so_far(c, ieri_s)
        delta = leads_oggi_so_far - leads_ieri_so_far

        sum_fw = sum(by_cd.get((c, d), {}).get("freq_w", 0.0) for d in last7)
        sum_iw = sum(by_cd.get((c, d), {}).get("imp_w", 0) for d in last7)
        freq_7g = (sum_fw / sum_iw) if sum_iw > 0 else None

        if freq_7g is None: freq_level = None
        elif freq_7g < 2.5: freq_level = "healthy"
        elif freq_7g < 3.5: freq_level = "monitor"
        elif freq_7g < 4.5: freq_level = "alta"
        else: freq_level = "critica"

        # Semaforo
        if spend_ieri == 0:
            sem = "nero"
        elif leads_ieri == 0 and spend_ieri > 0:
            sem = "rosso"
        elif cpl_ieri is not None and cpl_media is not None and cpl_ieri > 1.5 * cpl_media:
            sem = "rosso"
        elif cpl_ieri is not None and cpl_media is not None and cpl_ieri > cpl_media:
            sem = "giallo"
        else:
            sem = "verde"

        # date adset
        max_end = None
        min_start = None
        for s in camp_ends.get(c, []):
            try:
                d = datetime.fromisoformat(normalize_iso(s)).date()
                if max_end is None or d > max_end: max_end = d
            except: pass
        for s in camp_starts.get(c, []):
            try:
                d = datetime.fromisoformat(normalize_iso(s)).date()
                if min_start is None or d < min_start: min_start = d
            except: pass
        giorni_mancanti = (max_end - oggi).days if max_end else None

        # Rationale
        if sem == "verde":
            rationale = f"CPL {fmt_eur(cpl_ieri)} vs media {fmt_eur(cpl_media)}"
        elif sem in ("giallo", "rosso") and leads_ieri > 0 and cpl_media:
            dp = ((cpl_ieri - cpl_media) / cpl_media) * 100.0
            rationale = f"CPL {fmt_eur(cpl_ieri)} vs media {fmt_eur(cpl_media)} ({fmt_pct(dp)})"
        elif sem == "rosso" and leads_ieri == 0 and spend_ieri > 0:
            rationale = f"0 lead, spend {fmt_eur(spend_ieri)}"
        elif sem == "nero":
            rationale = "nessuna spesa registrata ieri"
        else:
            rationale = ""

        # Reading
        if sem == "nero":
            reading = "Nessuna spesa registrata ieri: la campagna non sta erogando."
        else:
            if leads_ieri == 0 and spend_ieri > 0:
                cpl_part = (f"Ieri {fmt_eur(spend_ieri)} spesi senza generare lead (media storica {fmt_eur(cpl_media)}/lead)"
                            if cpl_media else f"Ieri {fmt_eur(spend_ieri)} spesi senza generare lead")
            elif sem == "rosso" and leads_ieri > 0 and cpl_media:
                dp = ((cpl_ieri - cpl_media) / cpl_media) * 100.0
                cpl_part = f"Ieri CPL {fmt_eur(cpl_ieri)}, sopra soglia ({fmt_pct(dp)} sulla media {fmt_eur(cpl_media)})"
            elif sem == "giallo" and cpl_media:
                dp = ((cpl_ieri - cpl_media) / cpl_media) * 100.0
                cpl_part = f"Ieri CPL {fmt_eur(cpl_ieri)}, leggermente sopra media ({fmt_pct(dp)} su {fmt_eur(cpl_media)})"
            elif sem == "verde":
                cpl_part = f"Ieri CPL {fmt_eur(cpl_ieri)}, in linea con la media storica di {fmt_eur(cpl_media)}"
            else:
                cpl_part = f"Ieri CPL {fmt_eur(cpl_ieri) if cpl_ieri not in (None, float('inf')) else 'n/d'}"

            if delta > 0:
                trend_part = f"e oggi a quest'ora siamo già sopra ieri di {delta} lead"
            elif delta < 0:
                trend_part = f"ma oggi a quest'ora siamo sotto ieri di {abs(delta)} lead"
            elif leads_oggi_so_far > 0:
                trend_part = f"e oggi sta replicando lo stesso ritmo di ieri ({leads_oggi_so_far} lead)"
            else:
                trend_part = "e oggi non sono ancora arrivati lead (come ieri stesso orario)"

            reading = f"{cpl_part}, {trend_part}."
            if freq_level in ("alta", "critica"):
                reading += f" Frequenza {fmt_num_it(freq_7g)} impression/utente sugli ultimi 7gg: l'audience sta vedendo troppe volte gli stessi annunci, segnale di overlap/saturazione."

        # Path
        path_class = "default"
        if sem == "nero":
            path = "Verificare delivery: controllare pacing del budget, stato di approvazione degli annunci e bilancio account; se tutto ok, forzare riavvio con un piccolo bump del budget giornaliero."
            path_class = "urgent"
        elif giorni_mancanti is not None and giorni_mancanti <= 0:
            path = "Campagna in chiusura: lasciare correre fino all'end date senza modifiche e archiviare i learning per la prossima edizione."
            path_class = "soft"
        elif sem == "rosso" and delta < 0 and giorni_mancanti is not None and giorni_mancanti <= 2:
            path = "Intervento immediato: stoppare l'adset più costoso e spostare il budget sul top performer; con così pochi giorni residui evitare refresh creativi che non hanno tempo di apprendere."
            path_class = "urgent"
        elif sem == "rosso" and delta < 0:
            path = "Intervento immediato: sostituire creatività (nuovo visual + nuovo headline su un angle diverso) e restringere il targeting alle audience che hanno convertito meglio nei primi giorni."
            path_class = "urgent"
        elif sem == "rosso" and delta > 0:
            path = "Trend orario in recupero: monitorare nelle prossime 12-24h. Se domani il CPL non rientra in soglia, refresh completo della creatività."
            path_class = "urgent"
        elif sem == "rosso" and delta == 0 and leads_oggi_so_far == 0 and leads_ieri_so_far == 0:
            path = "Performance critica: zero lead oggi e ieri stesso orario. Sostituire creatività entro fine giornata e rivedere copy/headline del modulo Lead Ad."
            path_class = "urgent"
        elif sem == "rosso" and delta == 0:
            path = "Volumi stagnanti con CPL fuori soglia: avviare A/B test su nuova variante creativa entro oggi e rivedere targeting."
            path_class = "urgent"
        elif sem == "giallo" and delta < 0:
            path = "Testare una nuova variante creativa (visual o headline diverso) per riportare il CPL nella soglia di media; tenere il targeting invariato per ora."
            path_class = "default"
        elif sem == "giallo":
            path = "Lasciare correre con monitoraggio: il trend orario si sta autocorreggendo. Refresh solo se domani il CPL peggiora."
            path_class = "soft"
        elif sem == "verde" and leads_oggi_so_far == 0 and leads_ieri_so_far == 0:
            path = "Attendere la finestra serale (picco lead tipico 18-22). Se persiste zero, refresh visivo leggero domani mattina."
            path_class = "default"
        elif sem == "verde" and delta < 0:
            path = "Setup corretto, monitorare. Se il calo orario prosegue anche domani, refresh leggero del visual senza toccare il targeting."
            path_class = "default"
        elif sem == "verde" and delta > 0:
            path = "Mantenere setup invariato e, se il budget lo permette, alzare leggermente lo spending giornaliero per scalare il momentum."
            path_class = "soft"
        else:
            path = "Performance stabile in linea col CPL medio: lasciare correre senza modifiche."
            path_class = "soft"

        if freq_level == "critica":
            path += f" Inoltre frequenza 7gg {fmt_num_it(freq_7g)}: allargare subito l'audience — rimuovere interessi restrittivi, estendere il raggio geografico o creare lookalike 1-3% sui lead già acquisiti — per spostare la delivery su nuovi utenti e ridurre l'overlap."
            path_class = "urgent"
        elif freq_level == "alta":
            path += f" Inoltre frequenza 7gg {fmt_num_it(freq_7g)}: pianificare allargamento audience nelle prossime 48h (estensione geo o lookalike) per anticipare la fatigue."
            if path_class == "soft":
                path_class = "default"

        cards.append({
            "campaign": c, "sem": sem, "rationale": rationale, "reading": reading,
            "path": path, "path_class": path_class,
            "leads_oggi_so_far": leads_oggi_so_far, "leads_ieri_so_far": leads_ieri_so_far,
            "trend_3g": trend_3g, "freq_7g": freq_7g, "freq_level": freq_level,
            "giorni_mancanti": giorni_mancanti,
            "min_start": min_start.isoformat() if min_start else None,
            "max_end": max_end.isoformat() if max_end else None,
        })

    cards.sort(key=lambda c: (SEM_ORDER[c["sem"]],
                              c["giorni_mancanti"] if c["giorni_mancanti"] is not None else 9999))
    return cards


# ============================================================
# HTML rendering
# ============================================================

def render_card(c, cutoff_label):
    color = c["sem"]
    label = LABEL[color]
    partita = "n/d"
    if c["min_start"]:
        try:
            partita = date.fromisoformat(c["min_start"]).strftime("%d/%m/%Y")
        except: pass

    freq_html = ""
    if c["freq_7g"] is not None:
        freq_html = (f" · Frequency 7gg: <b>{fmt_num_it(c['freq_7g'])}</b>"
                     f"<span class=\"freq-badge freq-{c['freq_level']}\">{c['freq_level']}</span>")

    path_cls = c["path_class"] if c["path_class"] in ("urgent", "soft") else ""

    return f"""<div class="card {color}">
  <div class="card-head">
    <span class="tag {color}">{label}</span>
    <div class="camp-name">{esc(c['campaign'])}</div>
  </div>
  <div class="meta">Partita: {partita} · {giorni_text(c['giorni_mancanti'])}</div>
  <div class="rationale">{esc(c['rationale'])}</div>
  <div class="meta">Lead OGGI alle {cutoff_label}: <b>{c['leads_oggi_so_far']}</b> (ieri stesso orario {c['leads_ieri_so_far']})</div>
  <div class="meta">Trend ultimi 3 giorni: <b>{fmt_num_it(c['trend_3g'])}</b> lead media giornaliera{freq_html}</div>
  <div class="reading">{esc(c['reading'])}</div>
  <div class="path {path_cls}"><b>Cosa faremo per migliorare le performance:</b> {esc(c['path'])}</div>
</div>"""


def build_html(cards, oggi: date, cutoff_label: str, template_path: str):
    with open(template_path) as f:
        tpl = f.read()
    cards_html = "\n".join(render_card(c, cutoff_label) for c in cards)
    counts = {"rosso": 0, "giallo": 0, "verde": 0, "nero": 0}
    for c in cards:
        counts[c["sem"]] += 1
    out = (tpl.replace("{{DATA}}", oggi.strftime("%d/%m/%Y"))
              .replace("{{CUTOFF}}", cutoff_label)
              .replace("{{N_CAMPAGNE}}", str(len(cards)))
              .replace("{{CARDS_HTML}}", cards_html))
    return out, counts


# ============================================================
# Index page
# ============================================================

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Med &amp; Tech — Daily Check Campagne</title>
<style>
:root { color-scheme: light; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#f7f8fa; color:#1c2024; }
.container { max-width:760px; margin:0 auto; padding:40px 24px 60px; }
h1 { color:#0b57d0; font-size:24px; margin:0 0 6px; }
.subtitle { color:#5a5a5a; font-size:14px; margin:0 0 28px; }
.tagline { background:#fff; border-left:4px solid #0b57d0; padding:14px 18px; border-radius:0 6px 6px 0; margin-bottom:32px; font-size:13px; line-height:1.55; }
.section-label { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.8px; color:#6c757d; margin:0 0 10px; }
ul.report-list { list-style:none; padding:0; margin:0; background:#fff; border:1px solid #e3e6ea; border-radius:8px; overflow:hidden; }
li.report-item { border-bottom:1px solid #e3e6ea; }
li.report-item:last-child { border-bottom:none; }
li.report-item a { display:flex; align-items:center; justify-content:space-between; padding:14px 20px; text-decoration:none; color:#1c2024; transition:background .15s ease; }
li.report-item a:hover { background:#f0f3f7; color:#0b57d0; }
.day-name { font-weight:600; font-size:15px; }
.day-counts { font-size:12px; color:#6c757d; font-weight:500; margin-left:12px; }
.day-counts .c-rosso { color:#d93025; font-weight:700; }
.day-counts .c-giallo { color:#b76e00; font-weight:700; }
.day-counts .c-verde { color:#1e8e3e; font-weight:700; }
.day-counts .c-nero { color:#2a2a2a; font-weight:700; }
.arrow { color:#0b57d0; font-size:18px; font-weight:600; margin-left:auto; }
.empty { background:#fff; border:1px dashed #d6dce3; border-radius:8px; padding:30px; text-align:center; color:#6c757d; font-style:italic; }
.footer { text-align:center; font-size:11px; color:#6c757d; margin-top:30px; }
.footer a { color:#0b57d0; }
</style>
</head>
<body>
<div class="container">
  <h1>Med &amp; Tech — Daily Check Campagne</h1>
  <p class="subtitle">Archivio storico giornaliero · campagne Meta Lead Ads · Open Day Total Lift / Total Sculpt</p>
  <div class="tagline">Snapshot giornalieri generati automaticamente da <strong>FMM Consulting</strong>. Ogni report è una fotografia statica dei dati Windsor.ai al momento della generazione, con semafori CPL, trend orario e indicazioni operative.</div>
  <div class="section-label">Report disponibili</div>
{REPORT_LIST_HTML}
  <div class="footer">Aggiornato il {LAST_UPDATE} · <a href="https://fmmconsulting.it">FMM Consulting</a></div>
</div>
</body>
</html>"""

GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MESI_IT = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
           "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def rebuild_index(repo_root, oggi: date):
    """Scansiona la root del repo per file med-tech-daily-YYYY-MM-DD.html e ricostruisce index.html."""
    items = []
    for fn in os.listdir(repo_root):
        m = re.match(r"med-tech-daily-(\d{4})-(\d{2})-(\d{2})\.html$", fn)
        if not m: continue
        y, mo, d = map(int, m.groups())
        try:
            day = date(y, mo, d)
        except: continue
        # Try read counts from file
        counts = {"rosso": 0, "giallo": 0, "verde": 0, "nero": 0}
        try:
            with open(os.path.join(repo_root, fn), encoding="utf-8") as f:
                txt = f.read()
            for color in counts:
                counts[color] = len(re.findall(rf'class="card {color}"', txt))
        except: pass
        items.append((day, fn, counts))

    items.sort(key=lambda t: t[0], reverse=True)

    if not items:
        report_list = '  <div class="empty">Nessun report ancora pubblicato.</div>'
    else:
        rows = []
        for day, fn, counts in items:
            label = f"{GIORNI_IT[day.weekday()]} {day.day} {MESI_IT[day.month]} {day.year}"
            counts_html = (f'<span class="c-rosso">{counts["rosso"]}R</span> · '
                           f'<span class="c-giallo">{counts["giallo"]}G</span> · '
                           f'<span class="c-verde">{counts["verde"]}V</span> · '
                           f'<span class="c-nero">{counts["nero"]}N</span>')
            rows.append(
                f'    <li class="report-item">\n'
                f'      <a href="{fn}">\n'
                f'        <span class="day-name">{label}</span>\n'
                f'        <span class="day-counts">{counts_html}</span>\n'
                f'        <span class="arrow">→</span>\n'
                f'      </a>\n'
                f'    </li>'
            )
        report_list = '  <ul class="report-list">\n' + "\n".join(rows) + '\n  </ul>'

    out = (INDEX_TEMPLATE
           .replace("{REPORT_LIST_HTML}", report_list)
           .replace("{LAST_UPDATE}", oggi.strftime("%d/%m/%Y")))
    with open(os.path.join(repo_root, "index.html"), "w", encoding="utf-8") as f:
        f.write(out)


# ============================================================
# Git push helpers
# ============================================================

def git_run(repo_root, *args, check=True):
    return subprocess.run(["git", "-C", repo_root, *args],
                          capture_output=True, text=True, check=check)


def git_commit_push(repo_root, message):
    git_run(repo_root, "add", "-A")
    # Are there changes?
    status = git_run(repo_root, "status", "--porcelain", check=False)
    if not status.stdout.strip():
        return None
    git_run(repo_root, "commit", "-m", message)
    head = git_run(repo_root, "rev-parse", "HEAD")
    sha = head.stdout.strip()[:8]
    git_run(repo_root, "push", "origin", "main")
    return sha


# ============================================================
# Main
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-json", required=True)
    ap.add_argument("--hourly-json", required=True)
    ap.add_argument("--oggi", required=True, help="YYYY-MM-DD")
    ap.add_argument("--cutoff-hour", type=int, required=True)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--template",
                    default=None,
                    help="Path al template HTML (default: <repo_root>/_template/med-tech-daily-template.html)")
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    oggi = date.fromisoformat(args.oggi)
    cutoff_label = CUTOFF_LABEL_FMT.format(args.cutoff_hour)
    template_path = args.template or os.path.join(args.repo_root, "_template", "med-tech-daily-template.html")

    with open(args.daily_json) as f:
        daily = json.load(f)["result"]
    with open(args.hourly_json) as f:
        hourly = json.load(f)["result"]

    cards = compute_cards(daily, hourly, oggi, args.cutoff_hour)

    # Edge case: no campaigns pass filter → don't write file
    if not cards:
        print("N_CAMPAGNE=0")
        print("EMPTY=1")
        return

    html_out, counts = build_html(cards, oggi, cutoff_label, template_path)
    fn = f"med-tech-daily-{oggi.isoformat()}.html"
    fp = os.path.join(args.repo_root, fn)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(html_out)

    # Save daily KPI JSON snapshot (small, useful for backfills)
    data_dir = os.path.join(args.repo_root, "_data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, f"data-{oggi.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump({"oggi": oggi.isoformat(), "cutoff_hour": args.cutoff_hour,
                   "counts": counts, "cards": cards}, f, ensure_ascii=False, default=str, indent=2)

    rebuild_index(args.repo_root, oggi)

    sha = None
    if args.push:
        sha = git_commit_push(args.repo_root, f"Daily {oggi.isoformat()} — {counts['rosso']}R {counts['giallo']}G {counts['verde']}V {counts['nero']}N")

    url = f"{PAGES_BASE_URL}/{fn}"
    print(f"N_CAMPAGNE={len(cards)}")
    print(f"ROSSO={counts['rosso']}")
    print(f"GIALLO={counts['giallo']}")
    print(f"VERDE={counts['verde']}")
    print(f"NERO={counts['nero']}")
    print(f"FILENAME={fn}")
    print(f"URL={url}")
    if sha:
        print(f"COMMIT={sha}")


if __name__ == "__main__":
    main()
