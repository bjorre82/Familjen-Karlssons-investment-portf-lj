#!/usr/bin/env python3
"""
Familjen Karlsson - Portföljkontrollrum
Automatisk morgonuppdaterare (robust JSON + Stooq-kurser).

Körs av GitHub Actions varje morgon (och manuellt via "Run workflow"-knappen).

Gör tre saker:
  1. PORTFÖLJVÄRDE - hämtar riktiga kurser för innehaven från Stooq (gratis, ingen
     nyckel), växlar till SEK, räknar ut totalvärde + total avkastning, skriver in
     mellan <!--PORTF_VALUE_*--> och <!--PORTF_RETURN_*-->.
  2. MORNING BRIEFING + NYHETER - AI:n returnerar STRUKTURERAD DATA (JSON), scriptet
     bygger HTML från fasta mallar -> layouten kan aldrig brytas. Stora dagsrörelser
     highlightas (klass "mover"/"crash" + procent-badge).
  3. Uppdaterar datumstämplar.

INNEHAV som har riktig kurs (HOLDINGS nedan) styr portföljvärdet. Bygg på listan
när du köper fler aktier - lägg bara till en rad med antal, inköp och Stooq-symbol.

Kräver GitHub Secret: ANTHROPIC_API_KEY
Endast Pythons standardbibliotek.
"""

import os
import re
import sys
import csv
import json
import time
import io
import html as html_lib
import datetime
import urllib.request
import urllib.error

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
INDEX_FILE = "index.html"
MODEL = "claude-sonnet-4-5-20250929"

# ── INNEHAV (bygg på här när portföljen växer) ───────────────────────────────
# valuta: aktiens handelsvaluta. Stooq-symbol enligt stooq.com.
# antal: antal aktier. inkop: totalt inköpsvärde i SEK.
HOLDINGS = [
    {"namn": "ServiceNow", "antal": 50,  "inkop_sek": 47800, "stooq": ["now.us"],                          "valuta": "USD", "kort": "NOW"},
    {"namn": "Fortum Oyj", "antal": 100, "inkop_sek": 22032, "stooq": ["fum1v.fi", "fortum.fi", "fortum.he"], "valuta": "EUR", "kort": "FORTUM", "ai_fallback": True},
]


# ── Kurshämtning från Stooq (gratis, ingen nyckel) ───────────────────────────
def stooq_last(symbol):
    """Hämtar senaste stängningskurs för en Stooq-symbol. Returnerar float eller None."""
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            return None
        close = rows[0].get("Close", "")
        if close in ("", "N/D", "N/A"):
            return None
        return float(close)
    except Exception as e:
        print(f"  Stooq-fel för {symbol}: {e}")
        return None


def stooq_last_any(symbols):
    """Provar flera kandidatsymboler, returnerar (pris, symbol_som_funkade) eller (None, None)."""
    for sym in symbols:
        p = stooq_last(sym)
        if p is not None:
            return p, sym
    return None, None


def ai_price_eur(namn):
    """Reserv: frågar AI:n om aktiens aktuella kurs (EUR) via web search. Ungefärlig.
    Returnerar float eller None."""
    try:
        prompt = (f"Vad är den senaste aktiekursen för {namn} på Helsingforsbörsen just nu, "
                  f"i EUR? Sök med web search. Svara med ENBART ett tal (t.ex. 20.45), "
                  f"inget annat - ingen text, ingen valutasymbol.")
        raw = call_anthropic(prompt, max_tokens=300)
        # Plocka ut första talet ur svaret
        m = re.search(r'\d+[.,]?\d*', raw.replace(",", "."))
        if m:
            return float(m.group(0))
    except Exception as e:
        print(f"  AI-reserv misslyckades för {namn}: {e}")
    return None


def fx_to_sek(cur):
    """Växelkurs cur->SEK via Stooq. SEK=1.0."""
    if cur == "SEK":
        return 1.0
    sym = {"USD": "usdsek", "EUR": "eursek"}.get(cur)
    if not sym:
        return None
    return stooq_last(sym)


def compute_portfolio():
    """Returnerar (total_sek, inkop_sek, avk_pct, per_holding) eller None om data saknas.
    per_holding = {kort: {"nuv": SEK, "avk": pct}}"""
    total_sek = 0.0
    inkop_total = 0.0
    per_holding = {}
    ok = True
    for h in HOLDINGS:
        inkop_total += h["inkop_sek"]
        symbols = h["stooq"] if isinstance(h["stooq"], list) else [h["stooq"]]
        price, used = stooq_last_any(symbols)
        approx = False
        # Reserv: om Stooq inte hittar aktien och AI-reserv är påslagen, fråga AI:n
        if price is None and h.get("ai_fallback"):
            price = ai_price_eur(h["namn"])
            used = "AI-reserv (ungefärlig)"
            approx = True
        rate = fx_to_sek(h["valuta"])
        if price is None or rate is None:
            print(f"  Saknar kurs/FX för {h['namn']} (provade {symbols}, {h['valuta']})")
            ok = False
            continue
        värde = h["antal"] * price * rate
        total_sek += värde
        h_avk = (värde - h["inkop_sek"]) / h["inkop_sek"] * 100 if h["inkop_sek"] else 0.0
        per_holding[h["kort"]] = {"nuv": värde, "avk": h_avk, "approx": approx}
        print(f"  {h['namn']} [{used}]: {h['antal']} x {price} {h['valuta']} x {rate} = {värde:.0f} SEK ({h_avk:+.1f}%)")
    # Returnera ALLTID per_holding (så kort som funkar fylls), plus om allt löste sig
    avk = (total_sek - inkop_total) / inkop_total * 100 if inkop_total else 0.0
    return {"total_sek": total_sek, "inkop_sek": inkop_total, "avk": avk,
            "per_holding": per_holding, "alla_ok": ok}


# ── Anthropic API ────────────────────────────────────────────────────────────
def call_anthropic(prompt, max_tokens=4000, max_retries=5):
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    }
    data_bytes = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-api-key": API_KEY,
               "anthropic-version": "2023-06-01"}
    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                                     data=data_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=400) as resp:
                data = json.loads(resp.read())
            return "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text").strip()
        except urllib.error.HTTPError as e:
            last_err = e
            try:
                print(f"  API-fel {e.code}: {e.read().decode('utf-8')}")
            except Exception:
                pass
            if e.code in (429, 500, 503, 529):
                wait = (attempt + 1) * 30
                print(f"  Väntar {wait}s, försök {attempt + 1}/{max_retries}...")
                time.sleep(wait)
                continue
            raise
    raise last_err


PORTFOLIO_CONTEXT = """Du är portföljövervaknings-AI för Familjen Karlsson. En 15-20-årig
investeringsportfölj med hög risktolerans.
INNEHAV: ServiceNow (NOW, köpt), Fortum (FORTUM, köpt), ASML, Vinci, Skanska, Swedbank,
EQT, Investor, E.ON, Siemens Healthineers, IBM, IonQ, D-Wave, Rigetti, QuantumScape,
Amprius, Solid Power, Moog, TransDigm, Stora Enso, Keyence, COMPASS Pathways.
KÖPTRIGGERS: Hochtief <400 EUR, Vonovia <20 EUR, Holmen MA-brott ~360 SEK,
COMPASS COMP006 fas-3 Q3 2026, SpaceX IPO (köp ej noteringsdagen)."""


def get_data(date_str):
    prompt = f"""{PORTFOLIO_CONTEXT}

Datum: {date_str}.
Sök DAGENS och denna veckas nyheter med web search. Var särskilt uppmärksam på STORA
dagsrörelser (aktier som rör sig mer än +/-5% idag) - de är viktigast att rapportera.

Returnera ENDAST giltig JSON (ingen markdown):
{{
  "kopsignaler": [{{"typ":"gron|rod|bla","rubrik":"...","text":"..."}}],
  "nyheter_brief": [{{"ticker":"NOW","typ":"positiv|negativ|neutral","rubrik":"...","text":"..."}}],
  "atgarder": ["...","...","..."],
  "nyheter": [{{"ticker":"NOW","typ":"positiv|negativ|neutral","rubrik":"...","text":"...","rorelse_pct":-8.0}}]
}}

Regler:
- kopsignaler: 2-4 st. nyheter_brief: 4-6 korta. atgarder: exakt 3. nyheter: 6 st.
- "rorelse_pct" i nyheter: dagens procentuella kursrörelse om känd (t.ex. -8.0 eller 9.4),
  annars utelämna fältet. Aktier med stor rörelse ska ALLTID vara med bland nyheterna.
- Skriv på svenska, var konkret med siffror. INGEN HTML i texten - bara ren text."""
    raw = call_anthropic(prompt, 4000).replace("```json", "").replace("```", "").strip()
    a, b = raw.find("{"), raw.rfind("}")
    if a == -1 or b == -1:
        raise RuntimeError("Ingen JSON i svaret")
    return json.loads(raw[a:b + 1])


# ── HTML-byggare (fasta mallar = alltid balanserade) ─────────────────────────
def esc(s):
    return html_lib.escape(str(s), quote=True)


SIG_COLORS = {"gron": "#1A6B2A", "rod": "#8B0000", "bla": "#3b82f6"}
SIG_EMOJI = {"gron": "🟢", "rod": "🔴", "bla": "🚀"}
NEWS_BADGE = {"positiv": ("#DFF0D8", "#1A6B2A"), "negativ": ("#FDECEA", "#8B0000"),
              "neutral": ("#FFF3CD", "#B8860B")}
NEWS_DOT = {"positiv": "#22c55e", "negativ": "#ef4444", "neutral": "#f59e0b"}


def build_briefing(data, date_str):
    sig = ""
    for s in data.get("kopsignaler", []):
        c = SIG_COLORS.get(s.get("typ", "gron"), "#1A6B2A")
        e = SIG_EMOJI.get(s.get("typ", "gron"), "🟢")
        sig += (f'<div style="margin-bottom:7px"><b style="color:{c}">{e} '
                f'{esc(s.get("rubrik",""))}</b><br>{esc(s.get("text",""))}</div>')
    nb = ""
    for n in data.get("nyheter_brief", []):
        bg, fg = NEWS_BADGE.get(n.get("typ", "neutral"), NEWS_BADGE["neutral"])
        nb += (f'<div><span style="background:{bg};color:{fg};font-size:9px;font-weight:bold;'
               f'padding:1px 6px;border-radius:4px;margin-right:6px">{esc(n.get("ticker",""))}</span>'
               f'<b>{esc(n.get("rubrik",""))}</b> {esc(n.get("text",""))}</div>')
    atg = "".join(f'<div><b>{i+1}.</b> {esc(a)}</div>'
                  for i, a in enumerate(data.get("atgarder", [])[:3]))
    return f'''
    <div class="brf-sec" style="background:#F0FDF4;border-left:4px solid #1A6B2A">
      <div class="brf-sh" style="color:#1A6B2A">⚡ Köpsignaler och kritiska varningar</div>
      {sig}
    </div>
    <div class="brf-sec" style="background:#EFF6FF;border-left:4px solid #1F3864">
      <div class="brf-sh" style="color:#1F3864">📊 Viktiga nyheter</div>
      <div style="display:flex;flex-direction:column;gap:7px;font-size:11px">{nb}</div>
    </div>
    <div class="brf-sec" style="background:#FFFBEB;border-left:4px solid #B8860B">
      <div class="brf-sh" style="color:#B8860B">✅ Dagens 3 prioriterade åtgärder</div>
      <div style="font-size:11px;display:flex;flex-direction:column;gap:5px">{atg}</div>
    </div>
    <div style="font-size:9px;color:#888;text-align:center;margin-top:4px">Morning Briefing {esc(date_str)} · automatiskt genererad</div>'''


def build_news(data, date_str):
    cards = ""
    for n in data.get("nyheter", []):
        dot = NEWS_DOT.get(n.get("typ", "neutral"), "#f59e0b")
        # Stor rörelse -> highlight + badge
        cls = "nc"
        badge = ""
        pct = n.get("rorelse_pct")
        if isinstance(pct, (int, float)):
            if pct <= -5:
                cls = "nc crash"
                badge = f'<span class="movebadge down">▼ {pct:.1f}%</span>'
            elif pct >= 5:
                cls = "nc mover"
                badge = f'<span class="movebadge up">▲ +{pct:.1f}%</span>'
            elif pct < 0:
                badge = f'<span class="movebadge down">▼ {pct:.1f}%</span>'
            elif pct > 0:
                badge = f'<span class="movebadge up">▲ +{pct:.1f}%</span>'
        cards += f'''<div class="{cls}" onclick="this.classList.toggle('open')" style="border-left:3px solid {dot}">
  <div style="display:flex;gap:8px;align-items:flex-start">
    <div style="width:8px;height:8px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:4px"></div>
    <div style="flex:1">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:2px">
        <span class="nticker">{esc(n.get("ticker",""))}</span><span class="ndate">{esc(date_str)}</span>{badge}
      </div>
      <div class="nhl">{esc(n.get("rubrik",""))}</div>
      <div class="nbody">{esc(n.get("text",""))}</div>
    </div>
    <span class="narr">▼</span>
  </div>
</div>'''
    return cards


def replace_between(html, a, b, inner):
    s, e = html.find(a), html.find(b)
    if s == -1 or e == -1:
        raise RuntimeError(f"Markör saknas: {a}/{b}")
    return html[:s + len(a)] + inner + html[e:]


def fmt_sek(n):
    return f"{n:,.0f}".replace(",", " ") + " SEK"


# ── KURSKURVOR: hämta riktig daglig historik från Stooq ──────────────────────
# Ticker i korten -> kandidat-Stooq-symboler. USA (.us) funkar; vissa europeiska
# kan saknas (markeras då som "data saknas" istället för att visa påhittat).
CHART_SYMBOLS = {
    "ASML": ["asml.us"], "ARKK": ["arkk.us"], "NOW": ["now.us"],
    "EOAN": ["eoan.de"], "FORTUM": ["fum1v.fi", "fortum.fi"], "DG": ["dg.fr"],
    "SKAB": ["ska_b.se", "skab.se"], "HOT": ["hot.de"], "PEABB": ["peab_b.se"],
    "VNA": ["vna.de"], "SWEDA": ["swed_a.se", "sweda.se"], "EQT": ["eqt.se"],
    "INVEB": ["inve_b.se"], "SHL": ["shl.de"], "MDT": ["mdt.us"], "ISRG": ["isrg.us"],
    "TEM": ["tem.us"], "CMPS": ["cmps.us"], "IBM": ["ibm.us"], "IONQ": ["ionq.us"],
    "QBTS": ["qbts.us"], "RGTI": ["rgti.us"], "QS": ["qs.us"], "AMPX": ["ampx.us"],
    "SLDP": ["sldp.us"], "MOGA": ["mog_a.us", "moga.us"], "TDG": ["tdg.us"],
    "ASTS": ["asts.us"], "STERV": ["sterv.fi"], "HOLMB": ["holm_b.se"],
    "KYCCF": ["kyccf.us", "6861.jp"],
}


def fetch_daily_closes(symbol):
    """Daglig stängningshistorik från Stooq, äldst->nyast. None om saknas."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    rows = list(csv.DictReader(io.StringIO(text)))
    closes = []
    for row in rows:
        c = row.get("Close", "")
        if c not in (None, "", "N/D", "N/A"):
            try:
                closes.append(float(c))
            except ValueError:
                pass
    return closes if len(closes) >= 8 else None


def _sample_even(seq, n):
    if not seq:
        return []
    if len(seq) <= n:
        return seq[:]
    step = (len(seq) - 1) / (n - 1)
    return [seq[round(i * step)] for i in range(n)]


def build_periods(closes):
    """Bygger periodskivorna 1w/3m/12m/3y/5y ur dagliga closes."""
    def window(days, n):
        w = closes[-days:] if len(closes) >= days else closes[:]
        return [round(x, 2) for x in _sample_even(w, n)]
    return {
        "1w":  [round(x, 2) for x in closes[-7:]],
        "3m":  window(65, 13),
        "12m": window(252, 12),
        "3y":  window(756, 12),
        "5y":  window(1260, 20),
    }


def build_chart_data():
    """Hämtar riktig historik för alla tickers. Returnerar (pd_dict, ok_lista, fail_lista)."""
    pd = {}
    ok, fail = [], []
    for ticker, syms in CHART_SYMBOLS.items():
        closes = None
        for sym in syms:
            closes = fetch_daily_closes(sym)
            if closes:
                break
        if closes:
            pd[ticker] = build_periods(closes)
            ok.append(ticker)
            print(f"  Kurva {ticker}: {len(closes)} dagar -> riktig data")
        else:
            pd[ticker] = {"1w": [], "3m": [], "12m": [], "3y": [], "5y": []}
            fail.append(ticker)
            print(f"  Kurva {ticker}: SAKNAS (provade {syms})")
    return pd, ok, fail


def main():
    if not API_KEY:
        print("FEL: ANTHROPIC_API_KEY saknas", file=sys.stderr)
        sys.exit(1)

    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2)))
    wd = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
    mo = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
          "september", "oktober", "november", "december"]
    date_str = f"{wd[today.weekday()].capitalize()} {today.day} {mo[today.month-1]} {today.year}"
    print(f"Uppdaterar för: {date_str}")

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    # 1) PORTFÖLJVÄRDE
    print("Hämtar kurser från Stooq...")
    pf = compute_portfolio()
    per_holding = pf["per_holding"]
    # Fyll ALLTID i de kort vars kurs vi lyckades hämta (oberoende av varandra)
    for kort, v in per_holding.items():
        hup = v["avk"] >= 0
        cls = "pos" if hup else "neg"
        approx_tag = ' <span style="font-size:8px;color:#B8860B" title="Ungefärlig - AI-uppskattning, ej börskälla">~ungef.</span>' if v.get("approx") else ''
        nuv_html = f'{fmt_sek(v["nuv"])}{approx_tag}'
        avk_html = f'<span class="hv {cls}">{"▲ +" if hup else "▼ "}{v["avk"]:.1f}%</span>'
        try:
            html = replace_between(html, f"<!--NUV_{kort}_START-->", f"<!--NUV_{kort}_END-->", nuv_html)
            html = replace_between(html, f"<!--AVK_{kort}_START-->", f"<!--AVK_{kort}_END-->", avk_html)
        except RuntimeError:
            pass
    # Headerns totala portföljvärde: bara om ALLA innehav löste sig (annars vore det missvisande)
    if pf["alla_ok"]:
        total_sek, avk = pf["total_sek"], pf["avk"]
        up = avk >= 0
        col = "#4ade80" if up else "#f87171"
        any_approx = any(v.get("approx") for v in per_holding.values())
        approx_tag = ' <span style="font-size:8px;color:#fde68a" title="Innehåller AI-uppskattad kurs">~</span>' if any_approx else ''
        val_html = f'<span style="color:#fff">{fmt_sek(total_sek)}{approx_tag}</span>'
        ret_html = f'<span style="color:{col}">{"▲ +" if up else "▼ "}{avk:.2f}%</span>'
        html = replace_between(html, "<!--PORTF_VALUE_START-->", "<!--PORTF_VALUE_END-->", val_html)
        html = replace_between(html, "<!--PORTF_RETURN_START-->", "<!--PORTF_RETURN_END-->", ret_html)
        print(f"  Portföljvärde: {fmt_sek(total_sek)} ({avk:+.2f}%){' [innehåller AI-uppskattning]' if any_approx else ''}")
    else:
        print("  Vissa innehav saknar kurs - fyllde de kort som gick, lämnar headertotalen orörd.")

    # 2) BRIEFING + NYHETER
    print("Hämtar briefing + nyheter (web search)...")
    data = get_data(date_str)
    briefing_html = build_briefing(data, date_str)
    news_html = build_news(data, date_str)
    for label, frag in [("briefing", briefing_html), ("nyheter", news_html)]:
        if frag.count("<div") != frag.count("</div>"):
            print(f"VARNING: {label} obalanserad - avbryter", file=sys.stderr)
            sys.exit(1)
    html = replace_between(html, "<!--BRIEFING_START-->", "<!--BRIEFING_END-->", "\n" + briefing_html + "\n")
    html = replace_between(html, "<!--NEWS_START-->", "<!--NEWS_END-->", "\n" + news_html + "\n")

    # 2b) KURSKURVOR - hämta riktig historik och ersätt PD-objektet
    print("Hämtar kurskurvor från Stooq...")
    pd_data, ok_list, fail_list = build_chart_data()
    pd_json = json.dumps(pd_data, ensure_ascii=False)
    new_html, n = re.subn(r'const PD = \{.*?\}\};(\s*const PL)',
                          'const PD = ' + pd_json.replace('\\', '\\\\') + r';\1',
                          html, count=1, flags=re.DOTALL)
    if n == 1:
        html = new_html
        print(f"  Kurvor uppdaterade: {len(ok_list)} riktiga, {len(fail_list)} saknar data")
        if fail_list:
            print(f"  Saknar kursdata: {', '.join(fail_list)}")
    else:
        print("  VARNING: hittade inte PD-objektet - kurvor oförändrade")

    # 3) Datumstämplar
    html = re.sub(r'🌅 Morning Briefing — [^<]*', f'🌅 Morning Briefing — {date_str}', html, count=1)
    html = re.sub(r'<div class="news-hdr-s">[^<]*</div>',
                  f'<div class="news-hdr-s">Uppdaterat {date_str}</div>', html, count=1)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html uppdaterad.")


if __name__ == "__main__":
    main()

