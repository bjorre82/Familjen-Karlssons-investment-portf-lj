#!/usr/bin/env python3
"""
Familjen Karlsson - Portföljkontrollrum
Automatisk morning briefing-uppdaterare.

Körs av GitHub Actions varje morgon. Anropar Anthropic API EN gång med web search,
genererar både morning briefing och senaste nyheter i samma svar, och skriver in
dem i index.html mellan markörerna:
  <!--BRIEFING_START--> ... <!--BRIEFING_END-->
  <!--NEWS_START--> ... <!--NEWS_END-->

Ett enda API-anrop = håller sig under rate limit på nya/lägre API-tiers.

Kräver miljövariabel: ANTHROPIC_API_KEY  (sätts som GitHub Secret)
Använder endast Pythons standardbibliotek.
"""

import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.error

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
INDEX_FILE = "index.html"
MODEL = "claude-sonnet-4-5-20250929"

PORTFOLIO_CONTEXT = """Du är portföljövervaknings-AI för Familjen Karlsson. Detta är en 15-20-årig
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
- SpaceX (SPCX): IPO-nyheter - köp EJ noteringsdagen

Xetra-köp (E.ON, Hochtief, Vonovia, Siemens Healthineers) sker via Swedbank Mäklare."""


def call_anthropic(prompt, max_tokens=4000, max_retries=5):
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
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
            # Skriv ut API:ts faktiska felmeddelande (hjälper vid 400)
            try:
                err_body = e.read().decode("utf-8")
                print(f"  API-fel {e.code}: {err_body}")
            except Exception:
                pass
            if e.code in (429, 500, 503, 529):
                wait = (attempt + 1) * 30  # 30s, 60s, 90s, 120s, 150s
                print(f"  Väntar {wait}s och försöker igen "
                      f"(försök {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            raise
    raise last_err


def clean(t):
    return t.replace("```html", "").replace("```", "").strip()


def generate_all(date_str):
    """EN förfrågan som producerar både briefing och nyheter, åtskilda av en markör."""
    prompt = f"""{PORTFOLIO_CONTEXT}

Datum: {date_str}.

Sök DAGENS och denna veckas nyheter med web search för innehaven och triggernivåerna ovan.

Producera TVÅ HTML-block åtskilda av exakt raden:
===NEWS===

FÖRSTA BLOCKET = morning briefing. GILTIG HTML, exakt denna struktur:

<div class="brf-sec" style="background:#F0FDF4;border-left:4px solid #1A6B2A">
  <div class="brf-sh" style="color:#1A6B2A">⚡ Köpsignaler och kritiska varningar</div>
  <div style="margin-bottom:7px"><b style="color:#1A6B2A">🟢 Rubrik</b><br>Text.</div>
</div>
<div class="brf-sec" style="background:#EFF6FF;border-left:4px solid #1F3864">
  <div class="brf-sh" style="color:#1F3864">📊 Viktiga nyheter</div>
  <div style="display:flex;flex-direction:column;gap:7px;font-size:11px">
    <div><span style="background:#DFF0D8;color:#1A6B2A;font-size:9px;font-weight:bold;padding:1px 6px;border-radius:4px;margin-right:6px">TICKER</span><b>Rubrik.</b> Text.</div>
  </div>
</div>
<div class="brf-sec" style="background:#FFFBEB;border-left:4px solid #B8860B">
  <div class="brf-sh" style="color:#B8860B">✅ Dagens 3 prioriterade åtgärder</div>
  <div style="font-size:11px;display:flex;flex-direction:column;gap:5px">
    <div><b>1. ...</b> ...</div><div><b>2. ...</b> ...</div><div><b>3. ...</b> ...</div>
  </div>
</div>
<div style="font-size:9px;color:#888;text-align:center;margin-top:4px">Morning Briefing {date_str} · automatiskt genererad</div>

Köpsignaler: 2-4 st (🟢/🔴/🚀). Nyheter: 4-6 st; färger #DFF0D8/#1A6B2A positivt,
#FDECEA/#8B0000 negativt, #FFF3CD/#B8860B neutralt, #DBEAFE/#1D4ED8 info.

Sen raden:
===NEWS===

ANDRA BLOCKET = de 6 viktigaste nyheterna, en .nc-div per nyhet, exakt:

<div class="nc" onclick="this.classList.toggle('open')" style="border-left:3px solid FARG">
  <div style="display:flex;gap:8px;align-items:flex-start">
    <div style="width:8px;height:8px;border-radius:50%;background:FARG;flex-shrink:0;margin-top:4px"></div>
    <div style="flex:1">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:2px">
        <span class="nticker">TICKER</span><span class="ndate">{date_str}</span>
      </div>
      <div class="nhl">Rubrik</div>
      <div class="nbody">Längre brödtext med detaljer och siffror.</div>
    </div>
    <span class="narr">▼</span>
  </div>
</div>

FARG = #22c55e positivt, #ef4444 negativt, #f59e0b neutralt.

Skriv på svenska, var konkret med siffror. Returnera ENDAST de två blocken och
skiljeraden ===NEWS=== mellan dem. Ingen annan text."""
    return clean(call_anthropic(prompt, max_tokens=4000))


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
    print(f"Genererar briefing + nyheter för: {date_str}")

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    print("Hämtar briefing + nyheter i ett anrop (web search)...")
    result = generate_all(date_str)

    if "===NEWS===" in result:
        briefing_html, news_html = result.split("===NEWS===", 1)
    else:
        # Fallback: allt blir briefing, nyheter lämnas orörda
        briefing_html, news_html = result, None
        print("  VARNING: hittade ingen ===NEWS===-skiljare, uppdaterar bara briefing.")

    briefing_html = clean(briefing_html)
    html = replace_between(html, "<!--BRIEFING_START-->", "<!--BRIEFING_END-->", briefing_html)
    print(f"  Briefing: {len(briefing_html)} tecken")

    if news_html is not None:
        news_html = clean(news_html)
        html = replace_between(html, "<!--NEWS_START-->", "<!--NEWS_END-->", news_html)
        print(f"  Nyheter: {len(news_html)} tecken")

    import re
    html = re.sub(r'🌅 Morning Briefing — [^<]*',
                  f'🌅 Morning Briefing — {date_str}', html, count=1)
    html = re.sub(r'<div class="news-hdr-s">[^<]*</div>',
                  f'<div class="news-hdr-s">Uppdaterat {date_str}</div>', html, count=1)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html uppdaterad.")


if __name__ == "__main__":
    main()

