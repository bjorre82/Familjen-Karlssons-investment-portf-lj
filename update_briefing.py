#!/usr/bin/env python3
"""
Familjen Karlsson - Portföljkontrollrum
Automatisk morning briefing-uppdaterare (robust JSON-version).

Körs av GitHub Actions varje morgon. Anropar Anthropic API EN gång med web search.
AI:n returnerar STRUKTURERAD DATA (JSON) - inte HTML. Scriptet bygger sedan HTML:en
från fasta mallar, vilket garanterar att div-strukturen ALLTID är korrekt och att
sidans layout aldrig kan brytas, oavsett vad AI:n svarar.

Skriver in resultatet i index.html mellan markörerna:
  <!--BRIEFING_START--> ... <!--BRIEFING_END-->
  <!--NEWS_START--> ... <!--NEWS_END-->

Kräver miljövariabel: ANTHROPIC_API_KEY  (GitHub Secret)
Endast Pythons standardbibliotek - inga pip-beroenden.
"""

import os
import re
import sys
import json
import time
import html as html_lib
import datetime
import urllib.request
import urllib.error

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
INDEX_FILE = "index.html"
MODEL = "claude-sonnet-4-5-20250929"

PORTFOLIO_CONTEXT = """Du är portföljövervaknings-AI för Familjen Karlsson. En 15-20-årig
investeringsportfölj på ca 1 MSEK med hög risktolerans.

INNEHAV att bevaka: ServiceNow (NOW, köpt), Fortum (FORTUM, köpt), ASML, Vinci (DG),
Skanska, Swedbank, EQT, Investor, E.ON, Siemens Healthineers, IBM, IonQ, D-Wave,
Rigetti, QuantumScape, Amprius, Solid Power, Moog, TransDigm, Stora Enso, Keyence,
COMPASS Pathways.

KÖPTRIGGERS (kontrollera om nivå nåtts):
- Hochtief (HOT): KÖP om < 400 EUR
- Vonovia (VNA): KÖP om < 20 EUR
- Holmen (HOLM B): KÖP om kurs bryter 200-dagars MA (~360 SEK)
- COMPASS (CMPS): FDA-nyheter / COMP006 fas-3-data Q3 2026
- SpaceX (SPCX): IPO-nyheter - köp EJ noteringsdagen"""


def call_anthropic(prompt, max_tokens=4000, max_retries=5):
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    }
    data_bytes = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
    }
    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data_bytes, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=400) as resp:
                data = json.loads(resp.read())
            return "".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text"
            ).strip()
        except urllib.error.HTTPError as e:
            last_err = e
            try:
                print(f"  API-fel {e.code}: {e.read().decode('utf-8')}")
            except Exception:
                pass
            if e.code in (429, 500, 503, 529):
                wait = (attempt + 1) * 30
                print(f"  Väntar {wait}s och försöker igen ({attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            raise
    raise last_err


def esc(s):
    """HTML-escape så genererad text aldrig kan bryta strukturen."""
    return html_lib.escape(str(s), quote=True)


def get_data(date_str):
    """AI returnerar JSON. Scriptet bygger HTML. Strukturen kan aldrig brytas."""
    prompt = f"""{PORTFOLIO_CONTEXT}

Datum: {date_str}.

Sök DAGENS och denna veckas nyheter med web search för innehaven och triggernivåerna ovan.

Returnera ENDAST giltig JSON (ingen markdown, ingen text runtom) i EXAKT detta format:

{{
  "kopsignaler": [
    {{"typ": "gron", "rubrik": "Kort rubrik", "text": "Beskrivning med siffror."}}
  ],
  "nyheter_brief": [
    {{"ticker": "NOW", "typ": "positiv", "rubrik": "Kort.", "text": "Beskrivning."}}
  ],
  "atgarder": ["Åtgärd 1", "Åtgärd 2", "Åtgärd 3"],
  "nyheter": [
    {{"ticker": "NOW", "typ": "positiv", "rubrik": "Rubrik", "text": "Längre brödtext med detaljer och siffror."}}
  ]
}}

Regler:
- kopsignaler: 2-4 st. "typ" = "gron" (köpsignal), "rod" (varning) eller "bla" (info/IPO).
- nyheter_brief: 4-6 korta nyheter. "typ" = "positiv", "negativ" eller "neutral".
- atgarder: exakt 3 konkreta åtgärder för dagen.
- nyheter: de 6 viktigaste nyheterna med längre text. "typ" = "positiv"/"negativ"/"neutral".
- Skriv på svenska, var konkret med kurser och siffror.
- INGEN HTML i texten. Bara ren text. Returnera ENBART JSON-objektet."""
    raw = call_anthropic(prompt, max_tokens=4000)
    # Plocka ut JSON-objektet robust
    raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("Hittade ingen JSON i svaret")
    return json.loads(raw[start:end + 1])


# ── HTML-byggare (fasta mallar - alltid balanserade) ─────────────────────────
SIG_COLORS = {"gron": "#1A6B2A", "rod": "#8B0000", "bla": "#3b82f6"}
SIG_EMOJI = {"gron": "🟢", "rod": "🔴", "bla": "🚀"}
NEWS_BADGE = {
    "positiv": ("#DFF0D8", "#1A6B2A"),
    "negativ": ("#FDECEA", "#8B0000"),
    "neutral": ("#FFF3CD", "#B8860B"),
}
NEWS_DOT = {"positiv": "#22c55e", "negativ": "#ef4444", "neutral": "#f59e0b"}


def build_briefing(data, date_str):
    # Köpsignaler
    sig_rows = ""
    for s in data.get("kopsignaler", []):
        c = SIG_COLORS.get(s.get("typ", "gron"), "#1A6B2A")
        e = SIG_EMOJI.get(s.get("typ", "gron"), "🟢")
        sig_rows += (f'<div style="margin-bottom:7px"><b style="color:{c}">{e} '
                     f'{esc(s.get("rubrik",""))}</b><br>{esc(s.get("text",""))}</div>')

    # Nyheter (korta, i briefing)
    news_rows = ""
    for n in data.get("nyheter_brief", []):
        bg, fg = NEWS_BADGE.get(n.get("typ", "neutral"), NEWS_BADGE["neutral"])
        news_rows += (f'<div><span style="background:{bg};color:{fg};font-size:9px;'
                      f'font-weight:bold;padding:1px 6px;border-radius:4px;margin-right:6px">'
                      f'{esc(n.get("ticker",""))}</span><b>{esc(n.get("rubrik",""))}</b> '
                      f'{esc(n.get("text",""))}</div>')

    # Åtgärder
    atg = data.get("atgarder", [])[:3]
    atg_rows = "".join(
        f'<div><b>{i+1}.</b> {esc(a)}</div>' for i, a in enumerate(atg)
    )

    return f'''
    <div class="brf-sec" style="background:#F0FDF4;border-left:4px solid #1A6B2A">
      <div class="brf-sh" style="color:#1A6B2A">⚡ Köpsignaler och kritiska varningar</div>
      {sig_rows}
    </div>
    <div class="brf-sec" style="background:#EFF6FF;border-left:4px solid #1F3864">
      <div class="brf-sh" style="color:#1F3864">📊 Viktiga nyheter</div>
      <div style="display:flex;flex-direction:column;gap:7px;font-size:11px">
        {news_rows}
      </div>
    </div>
    <div class="brf-sec" style="background:#FFFBEB;border-left:4px solid #B8860B">
      <div class="brf-sh" style="color:#B8860B">✅ Dagens 3 prioriterade åtgärder</div>
      <div style="font-size:11px;display:flex;flex-direction:column;gap:5px">
        {atg_rows}
      </div>
    </div>
    <div style="font-size:9px;color:#888;text-align:center;margin-top:4px">Morning Briefing {esc(date_str)} · automatiskt genererad</div>'''


def build_news(data, date_str):
    cards = ""
    for n in data.get("nyheter", []):
        dot = NEWS_DOT.get(n.get("typ", "neutral"), "#f59e0b")
        cards += f'''<div class="nc" onclick="this.classList.toggle('open')" style="border-left:3px solid {dot}">
  <div style="display:flex;gap:8px;align-items:flex-start">
    <div style="width:8px;height:8px;border-radius:50%;background:{dot};flex-shrink:0;margin-top:4px"></div>
    <div style="flex:1">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:2px">
        <span class="nticker">{esc(n.get("ticker",""))}</span><span class="ndate">{esc(date_str)}</span>
      </div>
      <div class="nhl">{esc(n.get("rubrik",""))}</div>
      <div class="nbody">{esc(n.get("text",""))}</div>
    </div>
    <span class="narr">▼</span>
  </div>
</div>'''
    return cards


def replace_between(html, start_marker, end_marker, new_inner):
    s = html.find(start_marker)
    e = html.find(end_marker)
    if s == -1 or e == -1:
        raise RuntimeError(f"Markör saknas: {start_marker} / {end_marker}")
    return html[:s + len(start_marker)] + "\n" + new_inner + "\n" + html[e:]


def main():
    if not API_KEY:
        print("FEL: ANTHROPIC_API_KEY saknas", file=sys.stderr)
        sys.exit(1)

    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2)))
    weekdays = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
    months = ["januari", "februari", "mars", "april", "maj", "juni", "juli",
              "augusti", "september", "oktober", "november", "december"]
    date_str = f"{weekdays[today.weekday()].capitalize()} {today.day} {months[today.month-1]} {today.year}"
    print(f"Genererar för: {date_str}")

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    print("Hämtar data från API (web search)...")
    data = get_data(date_str)
    print(f"  Köpsignaler: {len(data.get('kopsignaler', []))}, "
          f"Nyheter-brief: {len(data.get('nyheter_brief', []))}, "
          f"Åtgärder: {len(data.get('atgarder', []))}, "
          f"Nyheter: {len(data.get('nyheter', []))}")

    briefing_html = build_briefing(data, date_str)
    news_html = build_news(data, date_str)

    # Säkerhetskontroll: div-balans innan vi skriver
    for label, frag in [("briefing", briefing_html), ("nyheter", news_html)]:
        o, c = frag.count("<div"), frag.count("</div>")
        if o != c:
            print(f"VARNING: {label} obalanserad ({o}/{c}) - hoppar över skrivning",
                  file=sys.stderr)
            sys.exit(1)

    html = replace_between(html, "<!--BRIEFING_START-->", "<!--BRIEFING_END-->", briefing_html)
    html = replace_between(html, "<!--NEWS_START-->", "<!--NEWS_END-->", news_html)

    html = re.sub(r'🌅 Morning Briefing — [^<]*',
                  f'🌅 Morning Briefing — {date_str}', html, count=1)
    html = re.sub(r'<div class="news-hdr-s">[^<]*</div>',
                  f'<div class="news-hdr-s">Uppdaterat {date_str}</div>', html, count=1)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html uppdaterad - layout garanterat intakt.")


if __name__ == "__main__":
    main()

