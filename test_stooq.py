#!/usr/bin/env python3
"""
STOOQ HISTORIK-TEST för Familjen Karlssons kontrollrum.

Detta script ändrar INGENTING - det rör varken index.html eller update_briefing.py.
Det bara provar att hämta historisk kursdata från Stooq för alla 31 bolag i korten
och skriver ut i loggen vilka som funkar och vilka som saknas.

Syfte: ta reda på (mot fakta, inte gissningar) hur många bolag vi kan ge RIKTIGA
kurvor innan vi bygger in det.

Körs via GitHub Actions. Endast Pythons standardbibliotek.
"""

import csv
import io
import urllib.request
import urllib.error

# Varje korts ticker -> lista med kandidat-symboler att prova hos Stooq.
# Stooq-konventioner: USA=.us, Tyskland=.de, Frankrike=.fr, Sverige=.se,
# Nederländerna=.nl, Finland=.fi, Japan=.jp
CANDIDATES = {
    "ASML":   ["asml.us", "asml.nl"],
    "ARKK":   ["arkk.us"],
    "NOW":    ["now.us"],
    "EOAN":   ["eoan.de", "eon.de"],
    "FORTUM": ["fum1v.fi", "fortum.fi", "fortum.he"],
    "DG":     ["dg.fr"],
    "SKAB":   ["ska_b.se", "skab.se", "ska-b.se"],
    "HOT":    ["hot.de"],
    "PEABB":  ["peab_b.se", "peabb.se"],
    "VNA":    ["vna.de"],
    "SWEDA":  ["swed_a.se", "sweda.se", "swed-a.se"],
    "EQT":    ["eqt.se"],
    "INVEB":  ["inve_b.se", "inveb.se", "inve-b.se"],
    "SHL":    ["shl.de"],
    "MDT":    ["mdt.us"],
    "ISRG":   ["isrg.us"],
    "TEM":    ["tem.us"],
    "CMPS":   ["cmps.us"],
    "IBM":    ["ibm.us"],
    "IONQ":   ["ionq.us"],
    "QBTS":   ["qbts.us"],
    "RGTI":   ["rgti.us"],
    "QS":     ["qs.us"],
    "AMPX":   ["ampx.us"],
    "SLDP":   ["sldp.us"],
    "MOGA":   ["mog_a.us", "moga.us"],
    "TDG":    ["tdg.us"],
    "ASTS":   ["asts.us"],
    "STERV":  ["sterv.fi", "sterv.he"],
    "HOLMB":  ["holm_b.se", "holmb.se"],
    "KYCCF":  ["kyccf.us", "6861.jp"],
}


def fetch_history(symbol):
    """Hämtar månadshistorik från Stooq. Returnerar antal datarader, eller 0."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=m"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)
    # Giltig respons har header "Date,Open,High,Low,Close,Volume" + datarader
    rows = list(csv.DictReader(io.StringIO(text)))
    valid = [r for r in rows if r.get("Close") not in (None, "", "N/D")]
    return len(valid), None


def main():
    print("=" * 55)
    print("STOOQ HISTORIK-TEST - provar alla 31 bolag")
    print("=" * 55)
    ok, fail = [], []
    for ticker, syms in CANDIDATES.items():
        found = None
        rows = 0
        for sym in syms:
            n, err = fetch_history(sym)
            if n >= 6:  # minst 6 månaders historik = användbar kurva
                found = sym
                rows = n
                break
        if found:
            print(f"  ✅ {ticker:7s} OK via {found:12s} ({rows} mån)")
            ok.append(ticker)
        else:
            print(f"  ❌ {ticker:7s} SAKNAS (provade {syms})")
            fail.append(ticker)

    print("=" * 55)
    print(f"SUMMERING: {len(ok)}/{len(CANDIDATES)} bolag har riktig historik")
    print(f"FUNKAR ({len(ok)}): {', '.join(ok)}")
    print(f"SAKNAS ({len(fail)}): {', '.join(fail)}")
    print("=" * 55)


if __name__ == "__main__":
    main()
