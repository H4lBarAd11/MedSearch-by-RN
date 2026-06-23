#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   MedSearch  v3.0                                                            ║
║   Comprehensive Medical & Scientific Literature Search Tool                  ║
║                                                                              ║
║   Author  : Riccardo Nevoso                                                  ║
║   Year    : 2026                                                             ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║   Databases : PubMed/MEDLINE · Scopus · Web of Science · Cochrane           ║
║               ClinicalTrials.gov · arXiv · medRxiv · bioRxiv                ║
║                                                                              ║
║   Access    : Open Access (Unpaywall) → DOI link → Sci-Hub (prompted)       ║
║                                                                              ║
║   AI        : Per-article one-liner · End-of-session synthesis               ║
║               Deep "Explain paper" mode — all via Claude                     ║
║                                                                              ║
║   Extras    : Export (Markdown / BibTeX / RIS) · Date filter                ║
║               Deduplication · MeSH suggestions · Journal quartile           ║
║               Search history · First-run config wizard                       ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, time, json, re, textwrap, shutil, subprocess
import urllib.parse, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  ANSI
# ══════════════════════════════════════════════════════════════════════════════

class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"; ITALIC = "\033[3m"
    RED = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    BLUE = "\033[94m"; MAGENTA = "\033[95m"; CYAN = "\033[96m"; GRAY = "\033[90m"

def c(col, t):  return f"{col}{t}{C.RESET}"
def bold(t):    return c(C.BOLD, t)
def dim(t):     return c(C.DIM + C.GRAY, t)
def italic(t):  return c(C.ITALIC, t)
def wrap(t, w=66, indent="    "):
    return ("\n" + indent).join(textwrap.wrap(str(t), w))

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  — file-based, falls back to env vars
# ══════════════════════════════════════════════════════════════════════════════

CONFIG_DIR  = Path.home() / ".medsearch"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "anthropic_api_key": "",
    "pubmed_api_key":    "",
    "scopus_api_key":    "",
    "wos_api_key":       "",
    "unpaywall_email":   "",
    "scihub_mirrors":    ["https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru"],
}

def load_config():
    cfg = dict(DEFAULTS)
    # 1. file
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            cfg.update(saved)
        except Exception:
            pass
    # 2. env vars override
    env_map = {
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "pubmed_api_key":    "NCBI_API_KEY",
        "scopus_api_key":    "SCOPUS_API_KEY",
        "wos_api_key":       "WOS_API_KEY",
        "unpaywall_email":   "UNPAYWALL_EMAIL",
    }
    for key, env in env_map.items():
        val = os.environ.get(env, "")
        if val:
            cfg[key] = val
    return cfg

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

CONFIG = load_config()

MAX_RESULTS_DEFAULT = 10
TIMEOUT = 15

# ══════════════════════════════════════════════════════════════════════════════
#  SESSION STATE
# ══════════════════════════════════════════════════════════════════════════════

COLLECTED_ARTICLES = []   # {title, authors, year, source, abstract, oneliner, doi, journal}
SEARCH_HISTORY     = []   # list of query strings
SEEN_DOIS          = set()  # for deduplication

# ══════════════════════════════════════════════════════════════════════════════
#  JOURNAL QUARTILE TABLE  (subset — top journals in medicine/biology)
# ══════════════════════════════════════════════════════════════════════════════

JOURNAL_QUARTILES = {
    # Q1
    "nature": "Q1", "science": "Q1", "cell": "Q1",
    "the lancet": "Q1", "lancet": "Q1",
    "new england journal of medicine": "Q1", "nejm": "Q1",
    "jama": "Q1", "jama network open": "Q1",
    "bmj": "Q1", "british medical journal": "Q1",
    "annals of internal medicine": "Q1",
    "nature medicine": "Q1", "nature biotechnology": "Q1",
    "nature genetics": "Q1", "nature communications": "Q1",
    "plos medicine": "Q1", "plos biology": "Q1",
    "journal of clinical oncology": "Q1",
    "circulation": "Q1", "european heart journal": "Q1",
    "gut": "Q1", "hepatology": "Q1",
    "journal of allergy and clinical immunology": "Q1",
    "american journal of respiratory and critical care medicine": "Q1",
    "diabetes care": "Q1", "diabetologia": "Q1",
    "annals of oncology": "Q1",
    "journal of infectious diseases": "Q1",
    "clinical infectious diseases": "Q1",
    "brain": "Q1", "annals of neurology": "Q1",
    "journal of neuroscience": "Q1",
    # Q2
    "plos one": "Q2", "plos genetics": "Q2",
    "scientific reports": "Q2",
    "bmc medicine": "Q2", "bmc bioinformatics": "Q2",
    "journal of internal medicine": "Q2",
    "european journal of clinical investigation": "Q2",
    "clinical microbiology and infection": "Q2",
    "journal of thrombosis and haemostasis": "Q2",
    # Q3 — add as needed
}

def get_quartile(journal_name):
    if not journal_name:
        return None
    key = journal_name.lower().strip()
    return JOURNAL_QUARTILES.get(key)

def quartile_badge(journal):
    q = get_quartile(journal)
    if not q:
        return ""
    colors = {"Q1": C.GREEN, "Q2": C.CYAN, "Q3": C.YELLOW, "Q4": C.GRAY}
    return c(colors.get(q, C.GRAY), f"[{q}]")

# ══════════════════════════════════════════════════════════════════════════════
#  HTTP HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def http_get(url, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "medsearch/3.0 (academic research)"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception:
        return None, 0

def fetch_json(url, headers=None):
    body, status = http_get(url, headers)
    if body:
        try: return json.loads(body), status
        except: pass
    return None, status

# ══════════════════════════════════════════════════════════════════════════════
#  TERMINAL UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def spinner(msg):
    sys.stdout.write(f"\r{C.CYAN}⠋{C.RESET} {msg}   ")
    sys.stdout.flush()

def clear_spinner():
    sys.stdout.write("\r" + " " * 76 + "\r")
    sys.stdout.flush()

def divider(char="─", width=74):
    print(c(C.GRAY, char * width))

def section_header(title, icon="🔬"):
    print(); divider("═")
    print(f"  {icon}  {bold(c(C.CYAN, title))}")
    divider("═")

def result_header(n, title, source):
    print(f"\n  {c(C.YELLOW, f'#{n}')}  {bold(title)}  {c(C.MAGENTA, f'[{source}]')}")

def access_badge(kind):
    return {"open":   c(C.GREEN,  "⬤ OPEN ACCESS"),
            "doi":    c(C.BLUE,   "⬤ DOI LINK"),
            "scihub": c(C.YELLOW, "⬤ SCI-HUB"),
            "none":   c(C.RED,    "⬤ NO LINK")}.get(kind, "")

def _open_url(url):
    for cmd in (["xdg-open"], ["open"], ["start"]):
        try:
            subprocess.Popen(cmd + [url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            print(f"    {dim('Opening in browser…')}")
            return
        except FileNotFoundError:
            continue
    print(f"    {dim('Copy this URL manually:')} {url}")

# ══════════════════════════════════════════════════════════════════════════════
#  ACCESS RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def check_open_access(doi):
    if not doi: return None
    email = CONFIG.get("unpaywall_email") or "research@example.com"
    url   = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={email}"
    spinner("Checking Unpaywall…")
    data, _ = fetch_json(url)
    clear_spinner()
    if data and data.get("is_oa"):
        loc = data.get("best_oa_location") or {}
        return loc.get("url_for_pdf") or loc.get("url")
    return None

def resolve_access(doi, fallback_url=None):
    oa = check_open_access(doi)
    if oa:    return "open", oa
    if doi:   return "doi",  f"https://doi.org/{doi}"
    if fallback_url: return "doi", fallback_url
    return "none", None

def display_access(kind, link, doi, title):
    print(f"    {access_badge(kind)}", end="  ")
    if kind == "open":
        print(c(C.GREEN, "Free full text:"))
        print(f"    {c(C.GREEN, link)}")
    elif kind == "doi":
        print(c(C.BLUE, "DOI / Publisher page:"))
        print(f"    {c(C.BLUE, link)}")
        if doi:
            mirror  = CONFIG["scihub_mirrors"][0]
            scihub  = f"{mirror}/{doi}"
            print(f"    {dim('Sci-Hub:')} {c(C.GRAY, scihub)}")
            ans = input(f"    {bold('Try Sci-Hub for full PDF? [y/N]:')} ").strip().lower()
            if ans == "y":
                _open_url(scihub)
    else:
        print(c(C.RED, "No link available."))

# ══════════════════════════════════════════════════════════════════════════════
#  DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def is_duplicate(doi, title):
    """Returns True if we've already seen this article."""
    if doi and doi in SEEN_DOIS:
        return True
    # Fuzzy title match (normalise)
    norm = re.sub(r"[^a-z0-9]", "", title.lower())
    if norm in SEEN_DOIS:
        return True
    return False

def register_article(doi, title):
    if doi:
        SEEN_DOIS.add(doi)
    norm = re.sub(r"[^a-z0-9]", "", title.lower())
    SEEN_DOIS.add(norm)

# ══════════════════════════════════════════════════════════════════════════════
#  AI  — one-liner, synthesis, explain
# ══════════════════════════════════════════════════════════════════════════════

def _claude(messages, max_tokens=100, stream=False):
    key = CONFIG.get("anthropic_api_key", "")
    if not key:
        return None
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "stream": stream,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={"x-api-key": key,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        method="POST"
    )
    try:
        return urllib.request.urlopen(req, timeout=60)
    except Exception:
        return None

def ai_oneliner(title, abstract):
    if not CONFIG.get("anthropic_api_key") or not abstract or abstract == "No abstract available.":
        return None
    prompt = (f"Title: {title}\n\nAbstract: {abstract}\n\n"
              "In exactly one sentence (≤25 words), state the key finding in plain language. "
              "No preamble, no quotation marks.")
    r = _claude([{"role": "user", "content": prompt}], max_tokens=80)
    if r:
        try:
            data = json.loads(r.read().decode())
            return data["content"][0]["text"].strip()
        except Exception:
            pass
    return None

def ai_synthesis(query):
    if not CONFIG.get("anthropic_api_key"):
        print(c(C.YELLOW, "  ⚠  No Anthropic API key — synthesis unavailable.")); return
    if not COLLECTED_ARTICLES:
        print(c(C.YELLOW, "  No articles collected.")); return

    section_header("AI Research Synthesis", "🤖")
    parts = []
    for i, a in enumerate(COLLECTED_ARTICLES, 1):
        ol = f" → {a['oneliner']}" if a.get("oneliner") else ""
        parts.append(
            f"[{i}] {a['title']} ({a['year']}, {a['source']})\n"
            f"    Authors: {a.get('authors','Unknown')}{ol}\n"
            f"    Abstract: {(a.get('abstract') or '')[:400]}"
        )
    prompt = (
        f'Literature search query: "{query}"\n\n'
        f"{len(COLLECTED_ARTICLES)} articles found:\n\n" + "\n\n".join(parts) + "\n\n"
        "Write a comprehensive academic synthesis (3–5 paragraphs). Cover: state of evidence, "
        "key findings, consensus points, controversies/gaps, clinical/research implications. "
        "Reference articles by their [number]."
    )
    r = _claude([{"role": "user", "content": prompt}], max_tokens=1200, stream=True)
    if not r:
        print(c(C.RED, "  AI synthesis failed.")); return

    print(f"\n  {italic(c(C.CYAN, f'Synthesising {len(COLLECTED_ARTICLES)} articles…'))}\n")
    col = 0
    try:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"): continue
            ps = line[5:].strip()
            if ps == "[DONE]": break
            try:
                chunk = json.loads(ps).get("delta", {}).get("text", "")
            except Exception:
                continue
            for ch in chunk:
                if ch == "\n":
                    print(); col = 0
                else:
                    if col == 0:
                        sys.stdout.write("    "); col = 4
                    sys.stdout.write(ch); col += 1
                    if col >= 70 and ch == " ":
                        print(); col = 0
            sys.stdout.flush()
    except Exception as e:
        print(c(C.RED, f"\n  Stream error: {e}"))
    print("\n")

def ai_explain(article):
    """Deep explanation of a single article."""
    if not CONFIG.get("anthropic_api_key"):
        print(c(C.YELLOW, "  ⚠  No Anthropic API key.")); return
    section_header(f"Explaining: {article['title'][:60]}…", "🔍")
    prompt = (
        f"Title: {article['title']}\n"
        f"Authors: {article.get('authors','Unknown')}\n"
        f"Year: {article.get('year','n.d.')}\n"
        f"Abstract: {article.get('abstract','No abstract.')}\n\n"
        "Please explain this paper in clear language. Cover:\n"
        "1. The research question and why it matters\n"
        "2. The methodology used\n"
        "3. The main findings\n"
        "4. Limitations and potential biases\n"
        "5. Clinical or research implications\n"
        "Be thorough but accessible to a medical professional."
    )
    r = _claude([{"role": "user", "content": prompt}], max_tokens=800, stream=True)
    if not r:
        print(c(C.RED, "  Failed.")); return

    col = 0
    print()
    try:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"): continue
            ps = line[5:].strip()
            if ps == "[DONE]": break
            try:
                chunk = json.loads(ps).get("delta", {}).get("text", "")
            except Exception:
                continue
            for ch in chunk:
                if ch == "\n":
                    print(); col = 0
                else:
                    if col == 0:
                        sys.stdout.write("    "); col = 4
                    sys.stdout.write(ch); col += 1
                    if col >= 70 and ch == " ":
                        print(); col = 0
            sys.stdout.flush()
    except Exception as e:
        print(c(C.RED, f"\n  Stream error: {e}"))
    print("\n")

def prompt_explain_mode():
    """After search, ask if user wants to explain any article."""
    if not COLLECTED_ARTICLES or not CONFIG.get("anthropic_api_key"):
        return
    print(f"\n  {dim('Press')} {bold('E')} {dim('to explain any article in depth, or Enter to continue.')}")
    ans = input(f"  {bold('Choice:')} ").strip().upper()
    if ans != "E":
        return
    print()
    for i, a in enumerate(COLLECTED_ARTICLES, 1):
        print(f"    {c(C.YELLOW, str(i))}. [{a['source']}] {a['title'][:65]}")
    print()
    try:
        idx = int(input(f"  {bold('Article number:')} ").strip()) - 1
        if 0 <= idx < len(COLLECTED_ARTICLES):
            ai_explain(COLLECTED_ARTICLES[idx])
        else:
            print(c(C.RED, "  Invalid number."))
    except ValueError:
        pass

# ══════════════════════════════════════════════════════════════════════════════
#  MESH SUGGESTIONS
# ══════════════════════════════════════════════════════════════════════════════

def suggest_mesh(query):
    """Suggest MeSH terms for the query via NCBI."""
    spinner("Fetching MeSH suggestions…")
    url  = (f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/espell.fcgi"
            f"?db=pubmed&term={urllib.parse.quote(query)}&retmode=json")
    data, _ = fetch_json(url)
    url2 = (f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=mesh&term={urllib.parse.quote(query)}&retmax=5&retmode=json")
    mesh_data, _ = fetch_json(url2)
    clear_spinner()

    suggestions = []
    if data:
        corrected = data.get("esearchresult", {}).get("querytranslation", "")
        if corrected and corrected.lower() != query.lower():
            suggestions.append(f"PubMed translation: {corrected}")

    if mesh_data:
        ids = mesh_data.get("esearchresult", {}).get("idlist", [])
        if ids:
            fetch_url = (f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                         f"?db=mesh&id={','.join(ids[:5])}&retmode=xml")
            body, _ = http_get(fetch_url)
            if body:
                try:
                    root = ET.fromstring(body)
                    for term in root.findall(".//DescriptorName")[:5]:
                        suggestions.append(f"MeSH: {term.text}")
                except Exception:
                    pass

    if suggestions:
        print(f"\n  {bold(c(C.CYAN, '💡 MeSH / Query suggestions:'))}")
        for s in suggestions:
            print(f"    {c(C.CYAN, '·')} {s}")
        print()

# ══════════════════════════════════════════════════════════════════════════════
#  ARTICLE COLLECTOR
# ══════════════════════════════════════════════════════════════════════════════

def _collect(title, authors, year, source, abstract, doi=None, journal=None):
    """Get AI one-liner, deduplicate, register."""
    if is_duplicate(doi, title):
        print(f"    {dim('⟳ Duplicate — already shown from another source, skipping.')}")
        return False
    register_article(doi, title)

    spinner("Getting AI one-liner…")
    oneliner = ai_oneliner(title, abstract)
    clear_spinner()
    if oneliner:
        print(f"    {c(C.MAGENTA, '✦ AI:')} {italic(c(C.MAGENTA, oneliner))}")

    COLLECTED_ARTICLES.append({
        "title": title, "authors": authors, "year": year, "source": source,
        "abstract": abstract, "oneliner": oneliner, "doi": doi, "journal": journal,
    })
    return True

# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def export_results(query, synthesis_text=""):
    if not COLLECTED_ARTICLES:
        print(c(C.YELLOW, "  Nothing to export.")); return

    section_header("Export Results", "💾")
    print(f"  {c(C.YELLOW, '1.')} Markdown report")
    print(f"  {c(C.YELLOW, '2.')} BibTeX (.bib)")
    print(f"  {c(C.YELLOW, '3.')} RIS  (.ris)")
    print(f"  {c(C.YELLOW, '4.')} All three")
    fmt = input(f"\n  {bold('Format [1-4]:')} ").strip()
    if fmt not in ("1","2","3","4"):
        print(c(C.RED, "  Cancelled.")); return

    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe  = re.sub(r"[^\w]+", "_", query)[:40]
    base  = Path.home() / "medsearch_exports"
    base.mkdir(parents=True, exist_ok=True)

    if fmt in ("1","4"):
        _export_markdown(query, base / f"medsearch_{safe}_{ts}.md", synthesis_text)
    if fmt in ("2","4"):
        _export_bibtex(base / f"medsearch_{safe}_{ts}.bib")
    if fmt in ("3","4"):
        _export_ris(base / f"medsearch_{safe}_{ts}.ris")

def _export_markdown(query, path, synthesis=""):
    lines = [
        f"# MedSearch Results\n",
        f"**Query:** {query}  ",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Articles:** {len(COLLECTED_ARTICLES)}\n",
        "---\n",
    ]
    for i, a in enumerate(COLLECTED_ARTICLES, 1):
        lines += [
            f"## {i}. {a['title']}",
            f"**Source:** {a['source']} | **Year:** {a['year']}",
            f"**Authors:** {a.get('authors','Unknown')}",
        ]
        if a.get("doi"):
            lines.append(f"**DOI:** https://doi.org/{a['doi']}")
        if a.get("oneliner"):
            lines.append(f"**Summary:** _{a['oneliner']}_")
        lines += [f"\n{a.get('abstract','')}\n", "---\n"]
    if synthesis:
        lines += ["\n## AI Synthesis\n", synthesis, "\n"]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {c(C.GREEN, '✓')} Markdown → {path}")

def _export_bibtex(path):
    entries = []
    for i, a in enumerate(COLLECTED_ARTICLES, 1):
        key = re.sub(r"[^\w]", "", (a.get("authors","anon").split(";")[0].split(",")[0]
                                    + str(a.get("year","")) + str(i)))
        doi_field = f"  doi = {{{a['doi']}}},\n" if a.get("doi") else ""
        entries.append(
            f"@article{{{key},\n"
            f"  title   = {{{a['title']}}},\n"
            f"  author  = {{{a.get('authors','Unknown')}}},\n"
            f"  year    = {{{a.get('year','n.d.')}}},\n"
            f"  journal = {{{a.get('journal','')}}},\n"
            f"{doi_field}"
            f"}}"
        )
    path.write_text("\n\n".join(entries), encoding="utf-8")
    print(f"  {c(C.GREEN, '✓')} BibTeX → {path}")

def _export_ris(path):
    lines = []
    for a in COLLECTED_ARTICLES:
        lines += [
            "TY  - JOUR",
            f"TI  - {a['title']}",
            f"AU  - {a.get('authors','Unknown')}",
            f"PY  - {a.get('year','n.d.')}",
            f"JO  - {a.get('journal','')}",
        ]
        if a.get("doi"):
            lines.append(f"DO  - {a['doi']}")
        if a.get("abstract"):
            lines.append(f"AB  - {a['abstract'][:500]}")
        lines.append("ER  -\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {c(C.GREEN, '✓')} RIS → {path}")

# ══════════════════════════════════════════════════════════════════════════════
#  DATE FILTER HELPER
# ══════════════════════════════════════════════════════════════════════════════

def get_date_filter():
    """Ask user for optional year range. Returns (year_from, year_to) or (None,None)."""
    print(f"\n  {bold('Date filter')} {dim('(press Enter to skip)')}")
    try:
        y_from = input(f"  From year: ").strip()
        y_to   = input(f"  To year:   ").strip()
        y_from = int(y_from) if y_from else None
        y_to   = int(y_to)   if y_to   else None
        return y_from, y_to
    except ValueError:
        return None, None

def within_date_range(year_str, y_from, y_to):
    if not y_from and not y_to: return True
    try:
        y = int(str(year_str)[:4])
        if y_from and y < y_from: return False
        if y_to   and y > y_to:   return False
        return True
    except Exception:
        return True   # can't determine → include

# ══════════════════════════════════════════════════════════════════════════════
#  PUBMED
# ══════════════════════════════════════════════════════════════════════════════

def search_pubmed(query, max_results=MAX_RESULTS_DEFAULT, y_from=None, y_to=None):
    section_header("PubMed / MEDLINE", "🧬")
    suggest_mesh(query)

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    kp   = f"&api_key={CONFIG['pubmed_api_key']}" if CONFIG.get("pubmed_api_key") else ""
    date_param = ""
    if y_from or y_to:
        df = f"{y_from or 1900}/01/01"
        dt = f"{y_to or 2099}/12/31"
        date_param = f"&mindate={df}&maxdate={dt}&datetype=pdat"

    spinner("Querying PubMed…")
    data, _ = fetch_json(
        f"{base}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(query)}"
        f"&retmax={max_results}&retmode=json{kp}{date_param}"
    )
    clear_spinner()
    if not data: print(c(C.RED, "  PubMed search failed.")); return

    ids = data.get("esearchresult", {}).get("idlist", [])
    total = data.get("esearchresult", {}).get("count", "?")
    print(f"  {dim(f'Total matches on PubMed: {total} — showing top {len(ids)}')}")
    if not ids: print(c(C.YELLOW, "  No results.")); return

    spinner("Fetching article details…")
    body, _ = http_get(f"{base}/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml{kp}")
    clear_spinner()
    if not body: print(c(C.RED, "  Fetch failed.")); return

    root = ET.fromstring(body)
    shown = 0
    for art in root.findall(".//PubmedArticle"):
        med    = art.find(".//MedlineCitation")
        art_el = med.find("Article") if med is not None else None
        if art_el is None: continue

        title   = "".join((art_el.find("ArticleTitle") or ET.Element("x")).itertext()) or "No title"
        journal = (art_el.find(".//Journal/Title") or ET.Element("x")).text or ""
        year    = (art_el.find(".//Journal/JournalIssue/PubDate/Year") or ET.Element("x")).text or "n.d."

        if not within_date_range(year, y_from, y_to): continue

        aus = []
        for au in art_el.findall(".//AuthorList/Author")[:3]:
            ln = au.find("LastName"); fn = au.find("ForeName")
            if ln is not None:
                aus.append(f"{ln.text}{', '+fn.text[0]+'.' if fn is not None else ''}")
        author_str = "; ".join(aus) + (" et al." if len(art_el.findall(".//AuthorList/Author")) > 3 else "")
        doi  = next((a.text for a in art.findall(".//ArticleId") if a.get("IdType") == "doi"), None)
        pmid = (art.find(".//MedlineCitation/PMID") or ET.Element("x")).text
        abs_el   = art_el.find(".//Abstract/AbstractText")
        abstract = "".join(abs_el.itertext()) if abs_el is not None else "No abstract available."

        shown += 1
        result_header(shown, title, "PubMed")
        qb = quartile_badge(journal)
        print(f"    {dim('Authors:')} {author_str}")
        print(f"    {dim('Journal:')} {journal} {qb}  {dim('Year:')} {year}")
        if doi:  print(f"    {dim('DOI:')}     https://doi.org/{doi}")
        if pmid: print(f"    {dim('PMID:')}    https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
        print(f"    {dim('Abstract:')} {c(C.GRAY, wrap(abstract[:300]+'…'))}")

        _collect(title, author_str, year, "PubMed", abstract, doi, journal)
        kind, link = resolve_access(doi)
        display_access(kind, link, doi, title)
        divider()
        time.sleep(0.35)

# ══════════════════════════════════════════════════════════════════════════════
#  ARXIV
# ══════════════════════════════════════════════════════════════════════════════

def search_arxiv(query, max_results=MAX_RESULTS_DEFAULT, y_from=None, y_to=None):
    section_header("arXiv", "📐")
    spinner("Querying arXiv…")
    body, _ = http_get(
        f"https://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(query)}"
        f"&max_results={max_results}&sortBy=relevance"
    )
    clear_spinner()
    if not body: print(c(C.RED, "  arXiv search failed.")); return

    ns    = {"a": "http://www.w3.org/2005/Atom"}
    root  = ET.fromstring(body)
    shown = 0
    for e in root.findall("a:entry", ns):
        published  = e.find("a:published", ns).text[:10]
        year       = published[:4]
        if not within_date_range(year, y_from, y_to): continue
        title      = e.find("a:title", ns).text.strip().replace("\n", " ")
        authors    = [a.find("a:name", ns).text for a in e.findall("a:author", ns)[:3]]
        author_str = "; ".join(authors) + (" et al." if len(e.findall("a:author", ns)) > 3 else "")
        summary    = e.find("a:summary", ns).text.strip()
        arxiv_id   = e.find("a:id", ns).text.strip()
        pdf_link   = arxiv_id.replace("/abs/", "/pdf/")

        shown += 1
        result_header(shown, title, "arXiv")
        print(f"    {dim('Authors:')}   {author_str}")
        print(f"    {dim('Published:')} {published}")
        print(f"    {dim('Abstract:')}  {c(C.GRAY, wrap(summary[:300]+'…'))}")
        _collect(title, author_str, year, "arXiv", summary, journal="arXiv")
        print(f"    {access_badge('open')}  {c(C.GREEN, 'PDF:')} {c(C.GREEN, pdf_link)}")
        divider()

# ══════════════════════════════════════════════════════════════════════════════
#  medRxiv / bioRxiv
# ══════════════════════════════════════════════════════════════════════════════

def search_biorxiv_family(query, server="medrxiv", max_results=MAX_RESULTS_DEFAULT,
                           y_from=None, y_to=None):
    label = "medRxiv" if server == "medrxiv" else "bioRxiv"
    section_header(label, "🏥" if server == "medrxiv" else "🧫")

    spinner(f"Querying {label} via NCBI PMC…")
    ncbi_url = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        f"?db=pmc&term={urllib.parse.quote(query)}+AND+{server}[filter]"
        f"&retmax={max_results}&retmode=json"
    )
    data, _ = fetch_json(ncbi_url)
    clear_spinner()

    if data:
        ids = data.get("esearchresult", {}).get("idlist", [])
        if ids:
            spinner("Fetching preprint details…")
            body, _ = http_get(
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                f"?db=pmc&id={','.join(ids)}&retmode=xml"
            )
            clear_spinner()
            if body:
                try:
                    root  = ET.fromstring(body)
                    shown = 0
                    for art in root.findall(".//article"):
                        title_el = art.find(".//article-title")
                        title    = "".join(title_el.itertext()).strip() if title_el is not None else "No title"
                        abs_el   = art.find(".//abstract")
                        abstract = "".join(abs_el.itertext()).strip() if abs_el is not None else "No abstract."
                        year_el  = art.find(".//pub-date/year")
                        year     = year_el.text if year_el is not None else "n.d."
                        if not within_date_range(year, y_from, y_to): continue
                        doi_el   = art.find(".//article-id[@pub-id-type='doi']")
                        doi      = doi_el.text if doi_el is not None else None
                        shown += 1
                        result_header(shown, title, label)
                        print(f"    {dim('Year:')} {year}")
                        print(f"    {dim('Abstract:')} {c(C.GRAY, wrap(abstract[:280]+'…'))}")
                        _collect(title, "See link", year, label, abstract, doi, label)
                        print(f"    {access_badge('open')}  {c(C.GREEN, 'Free preprint')}")
                        if doi: print(f"    {dim('DOI:')} https://doi.org/{doi}")
                        divider()
                    if shown: return
                except Exception:
                    pass

    # Fallback
    encoded = urllib.parse.quote(query.replace(" ", "+"))
    print(f"  {dim('Direct search (all preprints are open access):')}")
    print(f"  {access_badge('open')}  {c(C.GREEN, f'https://www.{server}.org/search/{encoded}')}")
    divider()

# ══════════════════════════════════════════════════════════════════════════════
#  CLINICALTRIALS.GOV
# ══════════════════════════════════════════════════════════════════════════════

def search_clinicaltrials(query, max_results=MAX_RESULTS_DEFAULT, y_from=None, y_to=None):
    section_header("ClinicalTrials.gov", "⚕️")
    spinner("Querying ClinicalTrials.gov…")
    url  = (f"https://clinicaltrials.gov/api/v2/studies"
            f"?query.term={urllib.parse.quote(query)}&pageSize={max_results}&format=json")
    data, status = fetch_json(url)
    clear_spinner()
    if not data: print(c(C.RED, f"  Failed (HTTP {status}).")); return

    shown = 0
    for study in data.get("studies", []):
        proto      = study.get("protocolSection", {})
        id_mod     = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        desc_mod   = proto.get("descriptionModule", {})
        design_mod = proto.get("designModule", {})
        dates_mod  = proto.get("statusModule", {})

        nct_id  = id_mod.get("nctId", "N/A")
        title   = id_mod.get("briefTitle", "No title")
        st      = status_mod.get("overallStatus", "Unknown")
        phases  = design_mod.get("phases", ["N/A"])
        phase_s = ", ".join(phases) if isinstance(phases, list) else str(phases)
        brief   = desc_mod.get("briefSummary", "No summary.")
        start   = dates_mod.get("startDateStruct", {}).get("date", "n.d.")
        year    = start[:4] if start != "n.d." else "n.d."
        if not within_date_range(year, y_from, y_to): continue

        shown += 1
        result_header(shown, title, "ClinicalTrials")
        print(f"    {dim('NCT:')} {nct_id}  {dim('Status:')} {st}  {dim('Phase:')} {phase_s}  {dim('Start:')} {start}")
        print(f"    {dim('Summary:')} {c(C.GRAY, wrap(brief[:280]+'…'))}")
        _collect(title, "ClinicalTrials.gov", year, "ClinicalTrials", brief)
        print(f"    {access_badge('open')}  {c(C.GREEN, f'https://clinicaltrials.gov/study/{nct_id}')}")
        divider()
        time.sleep(0.1)

    if not shown:
        print(c(C.YELLOW, "  No trials found matching date range."))

# ══════════════════════════════════════════════════════════════════════════════
#  COCHRANE
# ══════════════════════════════════════════════════════════════════════════════

def search_cochrane(query, max_results=MAX_RESULTS_DEFAULT, y_from=None, y_to=None):
    section_header("Cochrane Library", "🩺")
    # Cochrane does not offer a public API. The only programmatic access
    # is their search URL, which opens results in a browser.
    encoded  = urllib.parse.quote(query.replace(" ", "+"))
    coch_url = f"https://www.cochranelibrary.com/search?searchBy=6&searchText={encoded}"

    print(f"  {dim('Cochrane has no public API — opening search in browser.')}")
    print(f"  {dim('Full reviews require a personal or institutional subscription.')}")
    print()
    print(f"  {access_badge('doi')}  {bold('Cochrane search link:')}")
    print(f"  {c(C.BLUE, coch_url)}")
    print()
    ans = input(f"  {bold('Open in browser now? [y/N]:')} ").strip().lower()
    if ans == "y":
        _open_url(coch_url)
    divider()

# ══════════════════════════════════════════════════════════════════════════════
#  SCOPUS
# ══════════════════════════════════════════════════════════════════════════════

def search_scopus(query, max_results=MAX_RESULTS_DEFAULT, y_from=None, y_to=None):
    section_header("Scopus (Elsevier)", "📊")
    key = CONFIG.get("scopus_api_key", "")
    if not key:
        print(c(C.YELLOW, "  ⚠  No SCOPUS_API_KEY — run setup to add it."))
        encoded = urllib.parse.quote(query)
        print(f"\n  {access_badge('doi')}  {c(C.BLUE, f'https://www.scopus.com/search/form.uri?query={encoded}')}")
        divider(); return

    date_range = ""
    if y_from or y_to:
        df = y_from or 1900; dt = y_to or 2099
        date_range = f" AND PUBYEAR > {df-1} AND PUBYEAR < {dt+1}"

    spinner("Querying Scopus API…")
    url  = (f"https://api.elsevier.com/content/search/scopus"
            f"?query={urllib.parse.quote(query + date_range)}&count={max_results}"
            f"&apiKey={key}&httpAccept=application%2Fjson")
    data, status = fetch_json(url, headers={"X-ELS-APIKey": key, "Accept": "application/json"})
    clear_spinner()
    if not data or status != 200:
        print(c(C.RED, f"  Scopus API error (HTTP {status}).")); return

    for n, e in enumerate(data.get("search-results", {}).get("entry", []), 1):
        title    = e.get("dc:title", "No title")
        creator  = e.get("dc:creator", "Unknown")
        pub      = e.get("prism:publicationName", "")
        year     = e.get("prism:coverDate", "")[:4]
        doi      = e.get("prism:doi")
        cited    = e.get("citedby-count", "?")
        abstract = e.get("dc:description", "No abstract.")

        result_header(n, title, "Scopus")
        qb = quartile_badge(pub)
        print(f"    {dim('Author:')}   {creator}")
        print(f"    {dim('Journal:')}  {pub} {qb}  {dim('Year:')} {year}  {dim('Cited:')} {cited}")
        if doi: print(f"    {dim('DOI:')}     https://doi.org/{doi}")
        print(f"    {dim('Abstract:')} {c(C.GRAY, wrap(str(abstract)[:280]+'…'))}")
        _collect(title, creator, year, "Scopus", str(abstract), doi, pub)
        kind, link = resolve_access(doi)
        display_access(kind, link, doi, title)
        divider()
        time.sleep(0.2)

# ══════════════════════════════════════════════════════════════════════════════
#  WEB OF SCIENCE
# ══════════════════════════════════════════════════════════════════════════════

def search_wos(query, max_results=MAX_RESULTS_DEFAULT, y_from=None, y_to=None):
    section_header("Web of Science (Clarivate)", "🌐")
    key = CONFIG.get("wos_api_key", "")
    if not key:
        print(c(C.YELLOW, "  ⚠  No WOS_API_KEY — run setup to add it."))
        print(f"\n  {access_badge('doi')}  {c(C.BLUE, 'https://www.webofscience.com/wos/woscc/basic-search')}")
        divider(); return

    spinner("Querying Web of Science API…")
    url = (f"https://api.clarivate.com/apis/wos-starter/v1/documents"
           f"?db=WOS&q={urllib.parse.quote(query)}&limit={max_results}&page=1")
    data, status = fetch_json(url, headers={"X-ApiKey": key})
    clear_spinner()
    if not data or status != 200:
        print(c(C.RED, f"  WoS API error (HTTP {status}).")); return

    shown = 0
    for h in data.get("hits", []):
        src     = h.get("source", {})
        year    = str(src.get("publishYear", "n.d."))
        if not within_date_range(year, y_from, y_to): continue
        title   = h.get("title", "No title")
        journal = src.get("sourceTitle", "Unknown")
        doi     = next((i.get("value") for i in h.get("identifiers", []) if i.get("type") == "doi"), None)
        aus     = [a.get("displayName","") for a in h.get("names", {}).get("authors", [])[:3]]
        author_str = "; ".join(aus) + (" et al." if len(h.get("names", {}).get("authors", [])) > 3 else "")
        abstract = h.get("abstract", "No abstract.")

        shown += 1
        result_header(shown, title, "WoS")
        qb = quartile_badge(journal)
        print(f"    {dim('Authors:')} {author_str}")
        print(f"    {dim('Journal:')} {journal} {qb}  {dim('Year:')} {year}")
        if doi: print(f"    {dim('DOI:')}     https://doi.org/{doi}")
        print(f"    {dim('Abstract:')} {c(C.GRAY, wrap(str(abstract)[:280]+'…'))}")
        _collect(title, author_str, year, "Web of Science", str(abstract), doi, journal)
        kind, link = resolve_access(doi)
        display_access(kind, link, doi, title)
        divider()
        time.sleep(0.2)

# ══════════════════════════════════════════════════════════════════════════════
#  FIRST-RUN CONFIG WIZARD
# ══════════════════════════════════════════════════════════════════════════════

def run_setup_wizard(force=False):
    if CONFIG_FILE.exists() and not force:
        return  # already configured

    os.system("clear" if os.name != "nt" else "cls")
    print()
    print(c(C.CYAN, C.BOLD + """
  ╔══════════════════════════════════════════════════════════════════════╗
  ║           ⚙   MedSearch — First-Time Setup Wizard                  ║
  ╚══════════════════════════════════════════════════════════════════════╝
    """ + C.RESET))
    print(f"  Your settings will be saved to: {CONFIG_FILE}")
    print(f"  {dim('Press Enter to skip any key.')}\n")
    divider()

    fields = [
        ("anthropic_api_key",
         "Anthropic API key (for AI summaries & synthesis)",
         "Get free at: console.anthropic.com",
         True),
        ("pubmed_api_key",
         "NCBI / PubMed API key (optional — higher rate limits)",
         "Get free at: ncbi.nlm.nih.gov/account",
         True),
        ("scopus_api_key",
         "Scopus API key",
         "Get free at: dev.elsevier.com",
         True),
        ("wos_api_key",
         "Web of Science API key",
         "Apply at: developer.clarivate.com",
         True),
        ("unpaywall_email",
         "Your email for Unpaywall (open-access lookup)",
         "Any valid email works",
         False),
    ]

    new_cfg = dict(CONFIG)
    for key, label, hint, secret in fields:
        current = new_cfg.get(key, "")
        masked  = ("*" * (len(current)-4) + current[-4:]) if (secret and len(current) > 4) else current
        display = f" {dim(f'[current: {masked}]')}" if current else ""
        print(f"\n  {bold(label)}")
        print(f"  {dim(hint)}{display}")
        val = input(f"  → ").strip()
        if val:
            new_cfg[key] = val

    save_config(new_cfg)
    CONFIG.update(new_cfg)
    print(f"\n  {c(C.GREEN, '✓  Config saved!')}  You can re-run setup anytime from the menu.\n")
    input(f"  {dim('Press Enter to continue…')}")

# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH HISTORY
# ══════════════════════════════════════════════════════════════════════════════

def show_history():
    if not SEARCH_HISTORY:
        print(f"\n  {dim('No search history yet.')}\n"); return
    print(f"\n  {bold('Recent searches:')}")
    for i, q in enumerate(reversed(SEARCH_HISTORY[-10:]), 1):
        print(f"    {c(C.YELLOW, str(i))}. {q}")
    print()
    try:
        idx = input(f"  {bold('Re-run entry number (or Enter to cancel):')} ").strip()
        if idx:
            chosen = list(reversed(SEARCH_HISTORY[-10:]))[int(idx)-1]
            return chosen
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  MENU & MAIN
# ══════════════════════════════════════════════════════════════════════════════

SOURCES = {
    "1": ("PubMed / MEDLINE",   search_pubmed),
    "2": ("Scopus",             search_scopus),
    "3": ("Web of Science",     search_wos),
    "4": ("Cochrane Library",   search_cochrane),
    "5": ("ClinicalTrials.gov", search_clinicaltrials),
    "6": ("arXiv",              search_arxiv),
    "7": ("medRxiv",            lambda q, n=MAX_RESULTS_DEFAULT, yf=None, yt=None:
                                    search_biorxiv_family(q, "medrxiv", n, yf, yt)),
    "8": ("bioRxiv",            lambda q, n=MAX_RESULTS_DEFAULT, yf=None, yt=None:
                                    search_biorxiv_family(q, "biorxiv", n, yf, yt)),
    "A": ("ALL sources",        None),
}

def print_welcome():
    os.system("clear" if os.name != "nt" else "cls")
    ai_on = bool(CONFIG.get("anthropic_api_key"))
    ai_st = c(C.GREEN, "✓ AI ON") if ai_on else c(C.YELLOW, "○ AI OFF")
    print()
    print(c(C.CYAN, C.BOLD + """
  ╔══════════════════════════════════════════════════════════════════════╗
  ║          🔬  MedSearch v3  —  by Riccardo Nevoso                   ║
  ║       PubMed · Scopus · WoS · Cochrane · Trials · arXiv · Rxivs   ║
  ╚══════════════════════════════════════════════════════════════════════╝
    """ + C.RESET))
    print(f"  {ai_st}  {dim('|')}  {access_badge('open')} → {access_badge('doi')} → {access_badge('scihub')}")
    print()

def print_menu():
    divider()
    print(f"  {bold('Select database(s):')}")
    print()
    for k, (name, _) in SOURCES.items():
        print(f"    {c(C.YELLOW, k)}.  {name}")
    print()
    print(f"    {c(C.GRAY, 'H.  Search history')}")
    print(f"    {c(C.GRAY, 'S.  Settings / setup')}")
    print(f"    {c(C.GRAY, 'Q.  Quit')}")
    divider()

def run_search(choice, query, max_r, y_from, y_to):
    COLLECTED_ARTICLES.clear()
    SEEN_DOIS.clear()

    to_run = ([(name, fn) for k, (name, fn) in SOURCES.items() if k != "A"]
              if choice == "A" else [SOURCES[choice][:2]])

    for name, fn in to_run:
        try:
            fn(query, max_r, y_from, y_to)
        except TypeError:
            try: fn(query)
            except Exception as e:
                print(c(C.RED, f"  Error in {name}: {e}"))
        except Exception as e:
            print(c(C.RED, f"  Error in {name}: {e}"))

    print()
    divider("═")
    print(f"  {bold(c(C.CYAN,'📋 RESULTS'))}  "
          f"{dim('query:')} {query}  "
          f"{dim('found:')} {len(COLLECTED_ARTICLES)} unique articles  "
          f"{dim('time:')} {datetime.now().strftime('%H:%M:%S')}")
    divider("═")

    # Explain mode
    prompt_explain_mode()

    # AI synthesis
    synthesis_text = ""
    if COLLECTED_ARTICLES and CONFIG.get("anthropic_api_key"):
        ans = input(f"\n  {bold('Generate AI synthesis? [Y/n]:')} ").strip().lower()
        if ans != "n":
            ai_synthesis(query)
    elif COLLECTED_ARTICLES:
        print(f"\n  {dim('💡 Add ANTHROPIC_API_KEY in Settings for AI synthesis.')}")

    # Export
    ans = input(f"\n  {bold('Export results? [y/N]:')} ").strip().lower()
    if ans == "y":
        export_results(query, synthesis_text)

def main():
    run_setup_wizard()   # only runs if no config file exists
    print_welcome()

    while True:
        print()
        print_menu()
        choice = input(f"  {bold('Choice:')} ").strip().upper()

        if choice == "Q":
            print(f"\n  {dim('Goodbye!')}\n"); break

        if choice == "S":
            run_setup_wizard(force=True)
            print_welcome(); continue

        if choice == "H":
            rerun = show_history()
            if rerun:
                query = rerun
            else:
                continue
            choice = input(f"\n  {bold('Database for re-run:')} ").strip().upper()
            if choice not in SOURCES:
                print(c(C.RED, "  Invalid.")); continue
        else:
            if choice not in SOURCES:
                print(c(C.RED, "  Invalid choice.")); continue
            query = input(f"\n  {bold('Search query:')} ").strip()
            if not query:
                print(c(C.RED, "  Empty query.")); continue

        SEARCH_HISTORY.append(query)

        try:
            n_str = input(f"  {bold('Max results')} {dim('[default 10]')}: ").strip()
            max_r = int(n_str) if n_str else MAX_RESULTS_DEFAULT
        except ValueError:
            max_r = MAX_RESULTS_DEFAULT

        y_from, y_to = get_date_filter()

        run_search(choice, query, max_r, y_from, y_to)

        input(f"\n  {dim('Press Enter to search again…')}")
        print_welcome()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {dim('Interrupted. Goodbye!')}\n")
        sys.exit(0)
