#!/usr/bin/env python3
"""
MedSearch v4.0 — Flask Web GUI
Author: Riccardo Nevoso

Wraps all search logic from medsearch.py and serves a browser-based interface.
Results and AI text stream live via Server-Sent Events (SSE).
"""

import sys, os, json, re, time, threading, urllib.parse, urllib.request
import urllib.error, xml.etree.ElementTree as ET
import concurrent.futures
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, Response, jsonify, stream_with_context

# ── resolve paths so app.py works as a script AND as a frozen build ──────────
# Two freezing tools put bundled data files (templates/, VERSION) in different
# places:
#   • PyInstaller → a temp dir exposed as sys._MEIPASS
#   • py2app      → <Bundle>.app/Contents/Resources (sys.executable is in
#                   Contents/MacOS, so Resources is ../Resources)
# When run from source they sit next to this file. RESOURCE_DIR points at
# whichever actually holds the assets.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # PyInstaller
    RESOURCE_DIR = Path(sys._MEIPASS)
    APP_DIR_PATH = Path(sys.executable).parent.resolve()
elif getattr(sys, "frozen", False):
    # py2app: resources live in Contents/Resources, beside Contents/MacOS
    _exe_dir = Path(sys.executable).resolve().parent          # Contents/MacOS
    _res = _exe_dir.parent / "Resources"                       # Contents/Resources
    RESOURCE_DIR = _res if (_res / "templates").exists() else _exe_dir
    APP_DIR_PATH = _exe_dir
else:
    RESOURCE_DIR = Path(__file__).parent.resolve()
    APP_DIR_PATH = RESOURCE_DIR

BASE_DIR = RESOURCE_DIR
sys.path.insert(0, str(BASE_DIR))

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.secret_key = "medsearch_riccardo_2026"

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  (shared with medsearch.py logic)
# ══════════════════════════════════════════════════════════════════════════════

CONFIG_DIR  = Path.home() / ".medsearch"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "anthropic_api_key": "",
    "pubmed_api_key":    "",
    "scopus_api_key":    "",
    "scopus_insttoken":  "",
    "wos_api_key":       "",
    "unpaywall_email":   "",
    # User preference: whether AI features are active (independent of key).
    # Lets users who don't want AI turn it off even with a key present.
    "ai_enabled":        True,
    # Optional institutional proxy (EZProxy / OpenAthens). Paywalled-but-
    # subscribed papers can be opened through the user's library login.
    # institution_proxies: list of {"label","url"}; active_proxy: index into it.
    # institution_proxy (legacy single string) is migrated into the list on load.
    "institution_proxy": "",
    "institution_proxies": [],
    "active_proxy":     0,
    # Selected national guideline body (country code, e.g. "it"). Used by the
    # "National guidelines" button to open the right authority's search.
    "guideline_country": "",
    # Default source(s) for the menu-bar quick search. One of the source keys
    # ("pubmed", "guidelines", "scopus", ...) or "all". Editable from either app.
    "default_source": "pubmed",
    # Mirror priority order. sci-hub.se was DNS-blocked in Jan 2026, so the
    # currently-active mirrors come first. We pass the full list to the UI so
    # users can fall through to a backup if a mirror is unreachable.
    "scihub_mirrors":    ["https://sci-hub.ru", "https://sci-hub.st", "https://sci-hub.ee"],
}

# Legacy mirror configurations we silently migrate to the current defaults
# (old saved configs would otherwise keep pointing at the dead sci-hub.se).
_LEGACY_BROKEN_MIRRORS = [
    ["https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru"],
]

def load_config():
    cfg = dict(DEFAULTS)
    migrated = False
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            cfg.update(saved)
            # Migrate any config that still has the dead-mirror-first order
            if cfg.get("scihub_mirrors") in _LEGACY_BROKEN_MIRRORS:
                cfg["scihub_mirrors"] = list(DEFAULTS["scihub_mirrors"])
                migrated = True
            # Migrate a legacy single institution_proxy into the new list form.
            # If it matches UniTN's known proxy, tag it with that id so it lands
            # in the predefined UniTN slot instead of appearing as a custom dup.
            legacy_proxy = (cfg.get("institution_proxy") or "").strip()
            if legacy_proxy and not cfg.get("institution_proxies"):
                if "biblio.unitn.it" in legacy_proxy:
                    cfg["institution_proxies"] = [{"id": "unitn", "label": "UniTN", "url": legacy_proxy}]
                else:
                    cfg["institution_proxies"] = [{"label": "My institution", "url": legacy_proxy}]
                cfg["active_proxy"] = 0
                migrated = True
        except Exception: pass
    for k, e in {"anthropic_api_key":"ANTHROPIC_API_KEY","pubmed_api_key":"NCBI_API_KEY",
                 "scopus_api_key":"SCOPUS_API_KEY","wos_api_key":"WOS_API_KEY",
                 "unpaywall_email":"UNPAYWALL_EMAIL"}.items():
        v = os.environ.get(e,"")
        if v: cfg[k] = v
    if migrated:
        # persist the fix so it doesn't keep migrating each launch
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        except Exception: pass
    return cfg

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

CONFIG = load_config()

TIMEOUT = 15
MAX_RESULTS_DEFAULT = 10

# ── National clinical-guideline bodies ─────────────────────────────────────
# Per-country official guideline sources. We open these in the built-in browser
# (no scraping — their sites are JS-driven and/or anti-bot protected). Where the
# site accepts a query parameter we pre-fill it with {q}; otherwise we open the
# search/landing page for the user to type into. PubMed's "Guidelines" source
# already covers anything indexed there; these catch the national PDFs that
# aren't, and give clinicians the authoritative local source directly.
NATIONAL_GUIDELINE_BODIES = {
    "it": {
        "name": "SNLG — Sistema Nazionale Linee Guida",
        "country": "Italy",
        # Dedicated SNLG portal (the old /-/snlg path on iss.it is now dead). The
        # concluded-guidelines section lists downloadable PDFs; search is on-site
        # (JS-driven), so we open the portal rather than a pre-filled query.
        "url": "https://snlg.iss.it",
        "prefill": False,
    },
    "uk": {
        "name": "NICE — National Institute for Health and Care Excellence",
        "country": "United Kingdom",
        "url": "https://www.nice.org.uk/search?q={q}",
        "prefill": True,
    },
    "us": {
        "name": "ECRI Guidelines Trust",
        "country": "United States",
        # The US National Guideline Clearinghouse closed in 2018; ECRI is the
        # de-facto successor (free account required to view full guidelines).
        "url": "https://guidelines.ecri.org/",
        "prefill": False,
    },
    "de": {
        "name": "AWMF — Leitlinienregister",
        "country": "Germany",
        "url": "https://register.awmf.org/de/suche?searchterm={q}",
        "prefill": True,
    },
    "fr": {
        "name": "HAS — Haute Autorité de Santé",
        "country": "France",
        "url": "https://www.has-sante.fr/jcms/fc_2875171/fr/recherche?text={q}",
        "prefill": True,
    },
}


# ── Auto-update configuration ──────────────────────────────────────────────
# APP_DIR_PATH is set above (frozen-aware). VERSION ships as a bundled resource,
# so read it from RESOURCE_DIR; fall back to the app dir for source checkouts.
VERSION_FILE    = RESOURCE_DIR / "VERSION"
# Raw GitHub URL for the VERSION file on the main branch
GITHUB_RAW_VERSION = "https://raw.githubusercontent.com/H4lBarAd11/MedSearch-by-RN/main/VERSION"

def get_local_version():
    try:
        return VERSION_FILE.read_text().strip()
    except Exception:
        return "Beta 0"

def _version_tuple(v):
    """
    Parse a version string into a comparable tuple, where a real release
    always sorts ABOVE any beta.

    - 'Beta 6'  → (0, 6)      (betas are pre-1.0 releases)
    - '1.0'     → (1, 0)
    - '1.2.3'   → (1, 2, 3)

    This guarantees 1.0 > Beta N for every N, so users on a beta correctly
    receive the 1.0 update (and never get prompted to 'downgrade' to a beta).
    """
    s = str(v).strip()
    nums = re.findall(r"\d+", s)
    if not nums:
        return (0,)
    # Anything labelled "beta" is a pre-release: prefix a 0 major component.
    if re.search(r"beta", s, re.IGNORECASE):
        return (0,) + tuple(int(n) for n in nums)
    return tuple(int(n) for n in nums)

LOCAL_VERSION = get_local_version()

JOURNAL_QUARTILES = {
    "nature":"Q1","science":"Q1","cell":"Q1","the lancet":"Q1","lancet":"Q1",
    "new england journal of medicine":"Q1","nejm":"Q1","jama":"Q1",
    "jama network open":"Q1","bmj":"Q1","british medical journal":"Q1",
    "annals of internal medicine":"Q1","nature medicine":"Q1",
    "nature biotechnology":"Q1","nature genetics":"Q1","nature communications":"Q1",
    "plos medicine":"Q1","plos biology":"Q1","journal of clinical oncology":"Q1",
    "circulation":"Q1","european heart journal":"Q1","gut":"Q1","hepatology":"Q1",
    "journal of allergy and clinical immunology":"Q1",
    "american journal of respiratory and critical care medicine":"Q1",
    "diabetes care":"Q1","diabetologia":"Q1","annals of oncology":"Q1",
    "journal of infectious diseases":"Q1","clinical infectious diseases":"Q1",
    "brain":"Q1","annals of neurology":"Q1","journal of neuroscience":"Q1",
    "neurosurgery":"Q1","journal of neurosurgery":"Q1","acta neurochirurgica":"Q1",
    "world neurosurgery":"Q2","neurosurgical focus":"Q2",
    "plos one":"Q2","plos genetics":"Q2","scientific reports":"Q2",
    "bmc medicine":"Q2","bmc bioinformatics":"Q2","journal of internal medicine":"Q2",
    "european journal of clinical investigation":"Q2",
    "clinical microbiology and infection":"Q2",
}

def get_quartile(j):
    return JOURNAL_QUARTILES.get((j or "").lower().strip())

# ══════════════════════════════════════════════════════════════════════════════
#  HTTP HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def http_get(url, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "MedSearch/1.0 (academic literature search)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), r.status
    except urllib.error.HTTPError as e: return None, e.code
    except Exception: return None, 0

def fetch_json(url, headers=None, timeout=TIMEOUT):
    body, status = http_get(url, headers, timeout=timeout)
    if body:
        try: return json.loads(body), status
        except Exception: pass
    return None, status

# ══════════════════════════════════════════════════════════════════════════════
#  DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def make_dedup_set(): return set()

def is_duplicate(seen, doi, title):
    if doi and doi in seen: return True
    norm = re.sub(r"[^a-z0-9]","", title.lower())
    return norm in seen

def register(seen, doi, title):
    if doi: seen.add(doi)
    seen.add(re.sub(r"[^a-z0-9]","", title.lower()))

# ══════════════════════════════════════════════════════════════════════════════
#  ACCESS RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def _best_oa_url(locations):
    """
    Given a list of Unpaywall oa_location dicts, pick the URL most likely to
    actually fetch as a PDF. Publisher copies (Elsevier, Wiley, Springer...)
    frequently 403 a server-side fetch even when the article is open-access,
    whereas PubMed Central and other repositories serve PDFs reliably. So we
    rank: PMC/repository PDF > any direct PDF > repository landing > any URL.
    """
    if not locations:
        return None
    # Hosts that tend to block automated PDF fetches (deprioritize these)
    blocky = ("sciencedirect", "elsevier", "wiley", "springer", "tandfonline",
              "sagepub", "nature.com", "oup.com", "academic.oup", "cell.com",
              "jamanetwork", "nejm.org", "thelancet")
    # Hosts that serve PDFs reliably (prioritize these)
    friendly = ("ncbi.nlm.nih.gov", "europepmc", "pmc", "arxiv", "biorxiv",
                "medrxiv", "ssrn", "researchgate-not", "osf.io", "zenodo",
                "doaj", "plos", "frontiersin", "mdpi", "hindawi", "biomedcentral",
                ".edu", "repository", "repec")

    def host_of(u):
        try: return (urllib.parse.urlparse(u).hostname or "").lower()
        except Exception: return ""

    def score(loc):
        pdf = loc.get("url_for_pdf")
        url = loc.get("url")
        target = pdf or url
        if not target:
            return (-999, None)
        h = host_of(target)
        s = 0
        if pdf: s += 10                                  # direct PDF beats landing
        if any(f in h for f in friendly): s += 20        # reliable host
        if any(b in h for b in blocky):   s -= 15        # likely to 403
        if loc.get("host_type") == "repository": s += 5  # repos > publishers
        if loc.get("version") == "publishedVersion": s += 1
        return (s, target)

    ranked = sorted((score(l) for l in locations), key=lambda t: t[0], reverse=True)
    for s, target in ranked:
        if target:
            return target
    return None

def check_oa(doi):
    """
    Find a free full-text URL for a DOI. Tries Unpaywall first (preferring an OA
    copy that will actually fetch — PMC/repository over publisher, which often
    403s); falls back to OpenAlex when Unpaywall can't answer. Returns URL or None.
    """
    if not doi:
        return None
    # ── Unpaywall ──────────────────────────────────────────────────────────
    email = (CONFIG.get("unpaywall_email") or "").strip()
    unpaywall_answered = False
    if email:
        data, status = fetch_json(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}")
        if data is not None and status == 200:
            unpaywall_answered = True
            if data.get("is_oa"):
                # Consider ALL oa locations, not just best_oa_location, so we can
                # pick a repository/PMC copy that fetches without a 403.
                locs = data.get("oa_locations") or []
                if not locs and data.get("best_oa_location"):
                    locs = [data["best_oa_location"]]
                u = _best_oa_url(locs)
                if u:
                    return u
    # ── OpenAlex fallback ──────────────────────────────────────────────────
    if not unpaywall_answered:
        data2, _ = fetch_json(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}")
        if data2:
            oa = data2.get("open_access") or {}
            if oa.get("oa_url"):
                return oa["oa_url"]
            best = data2.get("best_oa_location") or {}
            if best.get("pdf_url"):
                return best["pdf_url"]
            if best.get("landing_page_url"):
                return best["landing_page_url"]
            for loc in (data2.get("locations") or []):
                if loc.get("is_oa") and loc.get("pdf_url"):
                    return loc["pdf_url"]
    return None

def _pmc_pdf_url(pmcid):
    """Build the direct PMC PDF URL from a PMC id (with or without the 'PMC' prefix)."""
    if not pmcid:
        return None
    p = str(pmcid).strip()
    if not p:
        return None
    if not p.upper().startswith("PMC"):
        p = "PMC" + p
    return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{p}/pdf/"

_PMC_LOOKUP_CACHE = {}
def pmcid_for_doi(doi):
    """
    Resolve a DOI to a PubMed Central id (or None) via NCBI's ID Converter API.
    PMC membership means a free full-text PDF exists at a public URL, even for
    papers whose 'home' is a paywalled publisher (e.g. ScienceDirect/Elsevier).
    Cached per-process and fails soft (returns None on any error/timeout).
    """
    if not doi:
        return None
    key = doi.lower().strip()
    if key in _PMC_LOOKUP_CACHE:
        return _PMC_LOOKUP_CACHE[key]
    pmcid = None
    try:
        url = ("https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
               f"?ids={urllib.parse.quote(doi)}&format=json&tool=medsearch&email=medsearch@example.org")
        # Short timeout: this is optional enrichment, so never let a slow NCBI
        # response hold up the search — fall back to the DOI link instead.
        data, status = fetch_json(url, timeout=6)
        if data and status == 200:
            recs = data.get("records") or []
            if recs:
                pmcid = recs[0].get("pmcid")  # e.g. "PMC1234567" or None if not in PMC
    except Exception:
        pmcid = None
    _PMC_LOOKUP_CACHE[key] = pmcid
    return pmcid

def resolve_access(doi):
    oa = check_oa(doi)
    if oa:  return "open", oa
    # No OA copy from Unpaywall/OpenAlex — but the paper may still be free in
    # PubMed Central (common for funded clinical research published in otherwise
    # paywalled journals like those on ScienceDirect). Check PMC by DOI and, if
    # present, route to the direct PMC PDF.
    pmcid = pmcid_for_doi(doi)
    if pmcid:
        pdf = _pmc_pdf_url(pmcid)
        if pdf:
            return "open", pdf
    if doi: return "doi",  f"https://doi.org/{doi}"
    return "none", None

def scihub_links(doi):
    """Return [primary_url, ...alternates] for a DOI, or [] if no DOI."""
    if not doi: return []
    mirrors = CONFIG.get("scihub_mirrors") or DEFAULTS["scihub_mirrors"]
    return [f"{m}/{doi}" for m in mirrors]

# ══════════════════════════════════════════════════════════════════════════════
#  AI
# ══════════════════════════════════════════════════════════════════════════════

def _claude(messages, max_tokens=100, stream=False):
    key = (CONFIG.get("anthropic_api_key","") or "").strip()
    if not key: return None
    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":max_tokens,
                          "stream":stream,"messages":messages}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key":key,"anthropic-version":"2023-06-01",
                 "content-type":"application/json"}, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        # Surface the API error so callers can report it
        try:
            detail = e.read().decode()
        except Exception:
            detail = ""
        raise RuntimeError(f"Anthropic API error {e.code}: {detail[:300]}")
    except Exception as e:
        raise RuntimeError(f"Anthropic request failed: {e}")

def ai_oneliner(title, abstract):
    if not (CONFIG.get("anthropic_api_key","") or "").strip() or not abstract: return None
    prompt = (f"Title: {title}\n\nAbstract: {abstract}\n\n"
              "In exactly one sentence (≤25 words), state the key finding. No preamble.")
    try:
        r = _claude([{"role":"user","content":prompt}], max_tokens=80)
        if r:
            return json.loads(r.read().decode())["content"][0]["text"].strip()
    except Exception:
        # one-liners fail silently (don't spam errors per-article); synthesis surfaces them
        return None
    return None

def ai_synthesis_stream(query, articles):
    """Generator that yields SSE chunks for the synthesis."""
    key = (CONFIG.get("anthropic_api_key","") or "").strip()
    if not key: yield "data: " + json.dumps({"type":"error","text":"No API key set. Add your Anthropic key in Settings."}) + "\n\n"; return
    if not articles: yield "data: " + json.dumps({"type":"error","text":"No articles."}) + "\n\n"; return

    parts = []
    for i,a in enumerate(articles,1):
        ol = f" → {a['oneliner']}" if a.get("oneliner") else ""
        parts.append(f"[{i}] {a['title']} ({a['year']}, {a['source']})\n"
                     f"    Authors: {a.get('authors','Unknown')}{ol}\n"
                     f"    Abstract: {(a.get('abstract') or '')[:400]}")
    prompt = (f'Literature search query: "{query}"\n\n'
              f"{len(articles)} articles:\n\n" + "\n\n".join(parts) + "\n\n"
              "Write a comprehensive academic synthesis (3–5 paragraphs). Cover: state of evidence, "
              "key findings, consensus, controversies/gaps, clinical implications. "
              "Reference articles by [number].")

    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":1200,"stream":True,
                          "messages":[{"role":"user","content":prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key":key,"anthropic-version":"2023-06-01",
                 "content-type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"): continue
                ps = line[5:].strip()
                if ps == "[DONE]": break
                try:
                    chunk = json.loads(ps).get("delta",{}).get("text","")
                    if chunk:
                        yield "data: " + json.dumps({"type":"chunk","text":chunk}) + "\n\n"
                except Exception: continue
    except urllib.error.HTTPError as e:
        try: detail = e.read().decode()
        except Exception: detail = ""
        msg = f"Anthropic API error {e.code}. "
        if e.code == 401:
            msg += "Your API key is invalid or expired. Re-enter it in Settings (check for typos or extra spaces)."
        elif e.code == 429:
            msg += "Rate limit or insufficient credits. Check your Anthropic account balance."
        else:
            msg += detail[:200]
        yield "data: " + json.dumps({"type":"error","text":msg}) + "\n\n"
    except Exception as e:
        yield "data: " + json.dumps({"type":"error","text":f"Request failed: {e}"}) + "\n\n"
    yield "data: " + json.dumps({"type":"done"}) + "\n\n"

def ai_explain_stream(article):
    key = CONFIG.get("anthropic_api_key","")
    if not key: yield "data: "+json.dumps({"type":"error","text":"No API key."})+"\n\n"; return
    prompt = (f"Title: {article['title']}\nAuthors: {article.get('authors','Unknown')}\n"
              f"Year: {article.get('year','n.d.')}\nAbstract: {article.get('abstract','No abstract.')}\n\n"
              "Explain this paper for a medical professional. Cover:\n"
              "1. Research question and why it matters\n2. Methodology\n"
              "3. Main findings\n4. Limitations and biases\n5. Clinical implications")
    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":900,"stream":True,
                          "messages":[{"role":"user","content":prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key":key,"anthropic-version":"2023-06-01",
                 "content-type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"): continue
                ps = line[5:].strip()
                if ps == "[DONE]": break
                try:
                    chunk = json.loads(ps).get("delta",{}).get("text","")
                    if chunk: yield "data: "+json.dumps({"type":"chunk","text":chunk})+"\n\n"
                except Exception: continue
    except Exception as e:
        yield "data: "+json.dumps({"type":"error","text":str(e)})+"\n\n"
    yield "data: "+json.dumps({"type":"done"})+"\n\n"

# ══════════════════════════════════════════════════════════════════════════════
#  AI ASSISTANT  (content-aware clinical chat, grounded in current results)
# ══════════════════════════════════════════════════════════════════════════════

def _articles_context(articles, limit=15, abstract_chars=300):
    """Build a compact text digest of the current results for grounding.

    Default mode (used for chat): drops abstracts, keeps title + one-liner.
    One-liners are AI-distilled summaries of the abstracts, so they capture
    the key finding in ~25 words instead of 300+. This shrinks per-turn
    context ~5x while preserving grounding quality.
    """
    if not articles:
        return "(No search results are currently loaded.)"
    parts = []
    for i, a in enumerate(articles[:limit], 1):
        line = f"[{i}] {a.get('title','')} ({a.get('year','n.d.')}, {a.get('journal','')})"
        if a.get("oneliner"):
            line += f"\n    → {a['oneliner']}"
        elif abstract_chars > 0 and a.get("abstract"):
            # Fallback when one-liners aren't available (no AI key, or AI failed)
            line += f"\n    Abstract: {(a.get('abstract') or '')[:abstract_chars]}"
        parts.append(line)
    extra = f"\n\n(+{len(articles)-limit} more results not shown)" if len(articles) > limit else ""
    return "\n\n".join(parts) + extra

ASSISTANT_SYSTEM = (
    "You are a clinical research assistant inside MedSearch, a medical literature "
    "search tool used by physicians and researchers. You help interpret evidence, "
    "answer clinical and scientific questions, and suggest directions for further "
    "inquiry.\n\n"
    "When the user's current search results are provided, ground your answers in "
    "them and cite specific papers by their bracket number, e.g. [3]. If you need "
    "the full abstract of a specific paper to answer well, tell the user to click "
    "'✦ Explain' on that card. If the results don't contain the answer, say so "
    "plainly and answer from general medical knowledge, making clear you're doing so.\n\n"
    "Be accurate, concise, and appropriately cautious. Default to 2-4 short "
    "paragraphs; expand only if asked. Note important uncertainties or "
    "contraindications. You are an aid to clinical reasoning, not a substitute for "
    "professional judgment; do not give individualized treatment directives for "
    "specific patients."
)

def assistant_chat_stream(messages, query, articles):
    """Stream a chat completion grounded in current results. `messages` is the
    running conversation [{role, content}, ...] from the client.

    Uses Anthropic prompt caching on the system block, so subsequent turns in
    the same conversation pay ~10x less for the article context portion.
    """
    key = (CONFIG.get("anthropic_api_key","") or "").strip()
    if not key:
        yield "data: " + json.dumps({"type":"error","text":"No API key set. Add your Anthropic key in Settings to use the assistant."}) + "\n\n"
        return

    context = _articles_context(articles)
    # Split system into a tiny instructions block (rarely changes) and a larger
    # context block (the articles). We mark the context block as cacheable.
    system_blocks = [
        {"type":"text", "text": ASSISTANT_SYSTEM},
        {"type":"text",
         "text": f"=== CURRENT SEARCH ===\nQuery: {query or '(none)'}\nResults currently loaded:\n{context}",
         "cache_control": {"type":"ephemeral"}}
    ]

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 512,            # 4-6 short paragraphs is plenty; ↓ from 1024
        "stream": True,
        "system": system_blocks,
        "messages": messages,
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key":key,
                 "anthropic-version":"2023-06-01",
                 "content-type":"application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"): continue
                ps = line[5:].strip()
                if ps == "[DONE]": break
                try:
                    chunk = json.loads(ps).get("delta",{}).get("text","")
                    if chunk:
                        yield "data: " + json.dumps({"type":"chunk","text":chunk}) + "\n\n"
                except Exception: continue
    except urllib.error.HTTPError as e:
        msg = f"Anthropic API error {e.code}. "
        if e.code == 401: msg += "Your API key is invalid or expired."
        elif e.code == 429: msg += "Rate limit or insufficient credits."
        yield "data: " + json.dumps({"type":"error","text":msg}) + "\n\n"
    except Exception as e:
        yield "data: " + json.dumps({"type":"error","text":f"Request failed: {e}"}) + "\n\n"
    yield "data: " + json.dumps({"type":"done"}) + "\n\n"

def assistant_suggestions(query, articles):
    """Generate 3-4 short follow-up questions based on the current search.
    Uses the cheaper Haiku model — this task doesn't need Sonnet's depth."""
    key = (CONFIG.get("anthropic_api_key","") or "").strip()
    if not key or not query:
        return []
    # Use a tighter context for suggestions — titles + one-liners only
    context = _articles_context(articles, limit=8, abstract_chars=0)
    prompt = (
        f'A clinician searched for: "{query}"\n\n'
        f"These results are loaded:\n{context}\n\n"
        "Suggest exactly 4 concise follow-up questions the clinician might want to "
        "ask about this evidence (comparisons, mechanisms, dosing, contraindications, "
        "gaps, guidelines, etc.). Each question ≤12 words, specific to this topic. "
        "Respond ONLY with a JSON array of 4 strings, nothing else."
    )
    try:
        # Use Haiku for this cheap task — ~12x cheaper than Sonnet
        payload = json.dumps({
            "model": "claude-haiku-4-5",
            "max_tokens": 250,
            "messages": [{"role":"user","content":prompt}]
        }).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
            headers={"x-api-key":key,"anthropic-version":"2023-06-01",
                     "content-type":"application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            text = json.loads(r.read().decode())["content"][0]["text"].strip()
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        arr = json.loads(text)
        if isinstance(arr, list):
            return [str(q).strip() for q in arr if str(q).strip()][:4]
    except Exception:
        return []
    return []

# ══════════════════════════════════════════════════════════════════════════════
#  MESH
# ══════════════════════════════════════════════════════════════════════════════

def get_mesh(query):
    suggestions = []
    data, _ = fetch_json(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/espell.fcgi"
                         f"?db=pubmed&term={urllib.parse.quote(query)}&retmode=json")
    if data:
        t = data.get("esearchresult",{}).get("querytranslation","")
        if t and t.lower() != query.lower(): suggestions.append({"type":"translation","text":t})
    mesh_data, _ = fetch_json(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                               f"?db=mesh&term={urllib.parse.quote(query)}&retmax=5&retmode=json")
    if mesh_data:
        ids = mesh_data.get("esearchresult",{}).get("idlist",[])
        if ids:
            body, _ = http_get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                                f"?db=mesh&id={','.join(ids[:5])}&retmode=xml")
            if body:
                try:
                    root = ET.fromstring(body)
                    for t in root.findall(".//DescriptorName")[:5]:
                        suggestions.append({"type":"mesh","text":t.text})
                except Exception: pass
    return suggestions

# ══════════════════════════════════════════════════════════════════════════════
#  CITATION GRAPH  (OpenCitations COCI API + Crossref title resolution)
# ══════════════════════════════════════════════════════════════════════════════

def _crossref_meta(doi):
    """Resolve a DOI to {title, year, authors} via Crossref. Returns None on failure."""
    if not doi: return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    data, status = fetch_json(url, headers={"User-Agent":"MedSearch/4.0 (research; mailto:research@example.com)"})
    if not data or status != 200:
        return None
    msg = data.get("message", {})
    title_list = msg.get("title", [])
    title = title_list[0] if title_list else "(title unavailable)"
    # Year
    year = ""
    for key in ("published-print","published-online","issued","created"):
        parts = msg.get(key, {}).get("date-parts", [[]])
        if parts and parts[0]:
            year = str(parts[0][0]); break
    # Authors (first 2)
    authors = []
    for a in msg.get("author", [])[:2]:
        fam = a.get("family",""); given = a.get("given","")
        if fam:
            authors.append(f"{fam}{' '+given[0]+'.' if given else ''}")
    n = len(msg.get("author", []))
    author_str = "; ".join(authors) + (" et al." if n > 2 else "")
    journal = (msg.get("container-title") or [""])[0]
    return {"doi": doi, "title": title, "year": year,
            "authors": author_str, "journal": journal}

def _resolve_dois(dois, limit=12):
    """Resolve up to `limit` DOIs to metadata, in parallel for speed."""
    dois = [d for d in dois if d][:limit]
    out = []
    if not dois: return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_crossref_meta, d): d for d in dois}
        for fut in concurrent.futures.as_completed(futures):
            try:
                meta = fut.result()
                if meta: out.append(meta)
            except Exception:
                pass
    return out

def get_citation_graph(doi, cap=12):
    """
    Returns {references:[...], citations:[...], counts:{...}} for a DOI.
    references = works this paper cites; citations = works citing this paper.
    Titles resolved via Crossref (capped for speed).
    """
    base = "https://opencitations.net/index/coci/api/v1"
    result = {"references": [], "citations": [],
              "ref_total": 0, "cit_total": 0, "doi": doi}
    if not doi:
        return result

    # References (outgoing — what this cites)
    ref_data, _ = fetch_json(f"{base}/references/{urllib.parse.quote(doi)}",
                             headers={"User-Agent":"MedSearch/4.0"})
    ref_dois = []
    if isinstance(ref_data, list):
        result["ref_total"] = len(ref_data)
        ref_dois = [r.get("cited","").replace("coci =>","").strip() for r in ref_data]
        ref_dois = [d for d in ref_dois if d]

    # Citations (incoming — what cites this)
    cit_data, _ = fetch_json(f"{base}/citations/{urllib.parse.quote(doi)}",
                             headers={"User-Agent":"MedSearch/4.0"})
    cit_dois = []
    if isinstance(cit_data, list):
        result["cit_total"] = len(cit_data)
        cit_dois = [r.get("citing","").replace("coci =>","").strip() for r in cit_data]
        cit_dois = [d for d in cit_dois if d]

    # Resolve titles (capped)
    result["references"] = _resolve_dois(ref_dois, limit=cap)
    result["citations"]  = _resolve_dois(cit_dois, limit=cap)
    return result

# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH FUNCTIONS  (return list of article dicts, no printing)
# ══════════════════════════════════════════════════════════════════════════════

def within_range(year_str, y_from, y_to):
    if not y_from and not y_to: return True
    try:
        y = int(str(year_str)[:4])
        if y_from and y < y_from: return False
        if y_to   and y > y_to:   return False
        return True
    except Exception: return True

def _pubmed_year(art_el):
    """Extract a 4-digit year from PubDate, handling <Year> and <MedlineDate>."""
    pd = art_el.find(".//Journal/JournalIssue/PubDate")
    if pd is None:
        return "n.d."
    y = pd.find("Year")
    if y is not None and y.text:
        return y.text
    md = pd.find("MedlineDate")   # e.g. "2020 Jan-Feb" or "1998-1999"
    if md is not None and md.text:
        m = re.search(r"\d{4}", md.text)
        if m: return m.group(0)
    return "n.d."

# Detect whether the user typed a "power query" (operators / field tags / quotes)
_PM_OPERATOR_RE = re.compile(r'\b(AND|OR|NOT)\b')          # Boolean operators (uppercase)
_PM_FIELDTAG_RE = re.compile(r'\[[a-zA-Z/ ]+\]')           # field tags like [tiab], [mesh], [au]
def is_power_query(q):
    """True if the query uses Boolean operators, field tags, or quoted phrases."""
    if _PM_OPERATOR_RE.search(q): return True
    if _PM_FIELDTAG_RE.search(q): return True
    if '"' in q: return True
    return False

def build_pubmed_term(query, strict=True):
    """
    Power query (operators/tags/quotes) → pass through verbatim, always.
        The user has taken explicit control; the strict flag is ignored.
    Strict (default) → AND the words together, each tagged [tiab] (title/abstract),
        so results are papers actually ABOUT the terms — not tangential MeSH-tree
        matches. e.g. 'glioblastoma temozolomide resistance'
                   → glioblastoma[tiab] AND temozolomide[tiab] AND resistance[tiab]
    Broad (opt-in) → bare terms; PubMed's Automatic Term Mapping expands to MeSH +
        synonyms (the pubmed.gov default). Wider recall, more drift.
    """
    q = query.strip()
    if is_power_query(q):
        return q
    if not strict:
        return q   # broad: let ATM expand freely
    # Strict: split into words, tag each [tiab], AND them.
    # Keep short multi-word as-is if only one token.
    words = [w for w in re.split(r'\s+', q) if w]
    if len(words) <= 1:
        return f"{q}[tiab]" if q else q
    return " AND ".join(f"{w}[tiab]" for w in words)

def search_pubmed(query, max_r, y_from, y_to, seen, strict=True,
                  extra_filter=None, source_label="PubMed", sort="relevance",
                  offset=0):
    results = []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    kp   = f"&api_key={CONFIG['pubmed_api_key']}" if CONFIG.get("pubmed_api_key") else ""
    dp   = (f"&mindate={y_from or 1900}/01/01&maxdate={y_to or 2099}/12/31&datetype=pdat"
            if y_from or y_to else "")
    term = build_pubmed_term(query, strict=strict)
    if extra_filter:
        term = f"({term}) AND {extra_filter}"
    # sort=relevance → PubMed "Best Match"; sort=date → most recent first
    sort_param = "date" if sort == "date" else "relevance"
    esearch = (f"{base}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(term)}"
               f"&retstart={offset}&retmax={max_r}&sort={sort_param}&retmode=json{kp}{dp}")
    data, _ = fetch_json(esearch)
    if not data: return results, 0
    ids   = data.get("esearchresult",{}).get("idlist",[])
    total = int(data.get("esearchresult",{}).get("count",0))
    if not ids: return results, total

    body, _ = http_get(f"{base}/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml{kp}")
    if not body: return results, total

    try:
        root = ET.fromstring(body)
    except Exception:
        return results, total

    # Preserve the relevance order returned by esearch
    articles_by_pmid = {}
    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//MedlineCitation/PMID")
        if pmid_el is not None and pmid_el.text:
            articles_by_pmid[pmid_el.text] = art

    ordered = [articles_by_pmid[i] for i in ids if i in articles_by_pmid]

    for art in ordered:
        med    = art.find(".//MedlineCitation")
        art_el = med.find("Article") if med is not None else None
        if art_el is None: continue

        title_el = art_el.find("ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else "No title"
        if not title: title = "No title"

        journal_el = art_el.find(".//Journal/Title")
        journal = journal_el.text if (journal_el is not None and journal_el.text) else ""

        year = _pubmed_year(art_el)
        if not within_range(year, y_from, y_to): continue

        aus = []
        for au in art_el.findall(".//AuthorList/Author")[:3]:
            ln = au.find("LastName"); fn = au.find("ForeName")
            if ln is not None and ln.text:
                initial = f", {fn.text[0]}." if (fn is not None and fn.text) else ""
                aus.append(f"{ln.text}{initial}")
        n_authors = len(art_el.findall(".//AuthorList/Author"))
        authors = "; ".join(aus) + (" et al." if n_authors > 3 else "")

        doi  = next((a.text for a in art.findall(".//ArticleId") if a.get("IdType")=="doi"), None)
        pmid_el = art.find(".//MedlineCitation/PMID")
        pmid = pmid_el.text if pmid_el is not None else None
        # PMC ID (when present) means the full text is free in PubMed Central —
        # we can build a direct PDF link, which is more complete/readable than
        # the publisher's page and saves the user a hop.
        pmcid = next((a.text for a in art.findall(".//ArticleId") if a.get("IdType")=="pmc"), None)

        # Abstract may have multiple labelled sections — join them all
        abs_parts = art_el.findall(".//Abstract/AbstractText")
        if abs_parts:
            chunks = []
            for ap in abs_parts:
                label = ap.get("Label")
                txt = "".join(ap.itertext())
                chunks.append(f"{label}: {txt}" if label else txt)
            abstract = " ".join(chunks).strip()
        else:
            abstract = ""

        if is_duplicate(seen, doi, title): continue
        register(seen, doi, title)
        # PubMed gives us the PMC id directly in its XML when the full text is
        # free in PubMed Central — use it for a direct PDF link without any extra
        # network lookup. Otherwise fall back to the normal OA/PMC resolution.
        if pmcid:
            kind, link = "open", _pmc_pdf_url(pmcid)
        else:
            kind, link = resolve_access(doi)
        scihub = scihub_links(doi)
        results.append({"title":title,"authors":authors,"year":year,"journal":journal,
                        "quartile":get_quartile(journal),"doi":doi,"pmid":pmid,
                        "abstract":abstract,"source":source_label,"access_kind":kind,
                        "access_link":link,"scihub":scihub,
                        "oneliner":None})
        time.sleep(0.12)
    return results, total

def search_cochrane(query, max_r, y_from, y_to, seen, strict=True, sort="relevance",
                    offset=0):
    """
    Cochrane systematic reviews are indexed in PubMed under the journal
    'Cochrane Database of Systematic Reviews'. We search PubMed restricted to
    that journal, giving real inline results instead of a dead external link.
    """
    # [ta] = journal title abbreviation field; covers the current journal name.
    cochrane_filter = '"Cochrane Database Syst Rev"[ta]'
    res, total = search_pubmed(query, max_r, y_from, y_to, seen,
                               strict=strict, extra_filter=cochrane_filter,
                               source_label="Cochrane", sort=sort, offset=offset)
    return res, total

def search_arxiv(query, max_r, y_from, y_to, seen, sort="relevance", offset=0):
    results = []
    # sortBy=relevance ↔ submittedDate (most recent first)
    sort_by = "submittedDate" if sort == "date" else "relevance"
    body, _ = http_get(f"https://export.arxiv.org/api/query?search_query=all:"
                       f"{urllib.parse.quote(query)}&start={offset}&max_results={max_r}&sortBy={sort_by}&sortOrder=descending")
    if not body: return results
    ns   = {"a":"http://www.w3.org/2005/Atom"}
    root = ET.fromstring(body)
    for e in root.findall("a:entry", ns):
        published = e.find("a:published",ns).text[:10]
        year = published[:4]
        if not within_range(year, y_from, y_to): continue
        title      = e.find("a:title",ns).text.strip().replace("\n"," ")
        authors    = [a.find("a:name",ns).text for a in e.findall("a:author",ns)[:3]]
        author_str = "; ".join(authors)+(" et al." if len(e.findall("a:author",ns))>3 else "")
        summary    = e.find("a:summary",ns).text.strip()
        arxiv_id   = e.find("a:id",ns).text.strip()
        pdf_link   = arxiv_id.replace("/abs/","/pdf/")
        if is_duplicate(seen, None, title): continue
        register(seen, None, title)
        results.append({"title":title,"authors":author_str,"year":year,"journal":"arXiv",
                        "quartile":None,"doi":None,"pmid":None,"abstract":summary,
                        "source":"arXiv","access_kind":"open","access_link":pdf_link,
                        "scihub":None,"oneliner":None})
    return results

def search_clinicaltrials(query, max_r, y_from, y_to, seen, sort="relevance", offset=0):
    results = []
    # ClinicalTrials v2: default ordering is relevance; LastUpdatePostDate:desc
    # gives most-recently-updated first.
    sort_p = "&sort=LastUpdatePostDate%3Adesc" if sort == "date" else ""
    data, _ = fetch_json(f"https://clinicaltrials.gov/api/v2/studies"
                         f"?query.term={urllib.parse.quote(query)}&pageSize={max_r + offset}&format=json{sort_p}")
    if not data: return results
    # The v2 API paginates by opaque token, not numeric offset, so for "load
    # more" we over-fetch (offset+max_r) and skip the first `offset` studies.
    studies = data.get("studies", [])
    if offset:
        studies = studies[offset:]
    for study in studies:
        proto  = study.get("protocolSection",{})
        id_mod = proto.get("identificationModule",{})
        sm     = proto.get("statusModule",{})
        dm     = proto.get("descriptionModule",{})
        des    = proto.get("designModule",{})
        nct    = id_mod.get("nctId","N/A")
        title  = id_mod.get("briefTitle","No title")
        status = sm.get("overallStatus","Unknown")
        phases = des.get("phases",["N/A"])
        phase  = ", ".join(phases) if isinstance(phases,list) else str(phases)
        brief  = dm.get("briefSummary","")
        start  = sm.get("startDateStruct",{}).get("date","n.d.")
        year   = start[:4] if start!="n.d." else "n.d."
        if not within_range(year, y_from, y_to): continue
        if is_duplicate(seen, None, title): continue
        register(seen, None, title)
        results.append({"title":title,"authors":"ClinicalTrials.gov","year":year,
                        "journal":f"Phase: {phase} | Status: {status}","quartile":None,
                        "doi":None,"pmid":None,"nct_id":nct,"abstract":brief,
                        "source":"ClinicalTrials","access_kind":"open",
                        "access_link":f"https://clinicaltrials.gov/study/{nct}",
                        "scihub":None,"oneliner":None})
        time.sleep(0.1)
    return results

# NOTE: medRxiv/bioRxiv were removed — their official API has no keyword-search
# endpoint (only date-range or DOI fetch), and the PMC-based workaround simply
# duplicated PubMed results via dedup. arXiv stays (it has a real search API).

def search_scopus(query, max_r, y_from, y_to, seen, sort="relevance", offset=0):
    results = []
    key = (CONFIG.get("scopus_api_key","") or "").strip()
    if not key:
        raise RuntimeError("No Scopus API key set.")
    dr = (f" AND PUBYEAR > {(y_from or 1900)-1} AND PUBYEAR < {(y_to or 2099)+1}"
          if y_from or y_to else "")
    # Scopus authenticates by API key PLUS institutional IP range. From off-campus
    # an institutional token (X-ELS-Insttoken) is also required — send it if set.
    headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
    insttoken = (CONFIG.get("scopus_insttoken","") or "").strip()
    if insttoken:
        headers["X-ELS-Insttoken"] = insttoken
    # sort=relevancy ↔ -coverDate (minus prefix = descending → newest first)
    sort_p = "&sort=-coverDate" if sort == "date" else "&sort=relevancy"
    url = (f"https://api.elsevier.com/content/search/scopus"
           f"?query={urllib.parse.quote(query+dr)}&start={offset}&count={max_r}{sort_p}")
    data, status = fetch_json(url, headers=headers)
    if status != 200:
        # Surface a clear, actionable error instead of failing silently
        if status == 401:
            raise RuntimeError("Scopus rejected the request (401). The API key may be wrong, "
                               "or you're off your institution's network — Scopus needs you on "
                               "the campus IP range, or an institutional token (set in config).")
        if status == 403:
            raise RuntimeError("Scopus access forbidden (403). Your key may lack entitlement "
                               "for the Search API, or your subscription doesn't cover it.")
        if status == 429:
            raise RuntimeError("Scopus quota exceeded (429). The weekly request limit for this "
                               "key is depleted; it resets ~1 week after first use.")
        if status == 400:
            raise RuntimeError("Scopus rejected the query (400) — likely a query-syntax issue.")
        raise RuntimeError(f"Scopus returned HTTP {status}.")
    if not data:
        return results
    for e in data.get("search-results",{}).get("entry",[]):
        # An error can also come back inside a 200 body
        if "error" in e:
            raise RuntimeError(f"Scopus: {e.get('error')}")
        title    = e.get("dc:title","No title")
        creator  = e.get("dc:creator","Unknown")
        pub      = e.get("prism:publicationName","")
        year     = e.get("prism:coverDate","")[:4]
        doi      = e.get("prism:doi")
        cited    = e.get("citedby-count","?")
        abstract = e.get("dc:description","")
        if is_duplicate(seen, doi, title): continue
        register(seen, doi, title)
        kind, link = resolve_access(doi)
        scihub = scihub_links(doi)
        results.append({"title":title,"authors":creator,"year":year,"journal":pub,
                        "quartile":get_quartile(pub),"doi":doi,"pmid":None,
                        "cited_by":cited,"abstract":abstract,"source":"Scopus",
                        "access_kind":kind,"access_link":link,"scihub":scihub,
                        "oneliner":None})
        time.sleep(0.2)
    return results

def search_wos(query, max_r, y_from, y_to, seen, sort="relevance", offset=0):
    results = []
    key = (CONFIG.get("wos_api_key","") or "").strip()
    if not key:
        raise RuntimeError("No Web of Science API key set.")
    # WoS Starter sortField: RS = Relevance, PY+D = Publication Year descending
    sort_p = "&sortField=PY%2BD" if sort == "date" else "&sortField=RS"
    # WoS paginates by 1-indexed page of size `limit`. Convert offset → page.
    wos_page = (offset // max_r) + 1 if max_r else 1
    data, status = fetch_json(
        f"https://api.clarivate.com/apis/wos-starter/v1/documents"
        f"?db=WOS&q={urllib.parse.quote(query)}&limit={max_r}&page={wos_page}{sort_p}",
        headers={"X-ApiKey":key})
    if status != 200:
        if status in (401, 403):
            raise RuntimeError(f"Web of Science rejected the request ({status}). The API key may "
                               "be wrong/expired, or not entitled to the WoS Starter API.")
        if status == 429:
            raise RuntimeError("Web of Science quota exceeded (429). Try again later.")
        raise RuntimeError(f"Web of Science returned HTTP {status}.")
    if not data:
        return results
    for h in data.get("hits",[]):
        src     = h.get("source",{})
        year    = str(src.get("publishYear","n.d."))
        if not within_range(year, y_from, y_to): continue
        title   = h.get("title","No title")
        journal = src.get("sourceTitle","")
        doi     = next((i.get("value") for i in h.get("identifiers",[]) if i.get("type")=="doi"),None)
        aus     = [a.get("displayName","") for a in h.get("names",{}).get("authors",[])[:3]]
        authors = "; ".join(aus)+(" et al." if len(h.get("names",{}).get("authors",[]))>3 else "")
        abstract = h.get("abstract","")
        if is_duplicate(seen, doi, title): continue
        register(seen, doi, title)
        kind, link = resolve_access(doi)
        scihub = scihub_links(doi)
        results.append({"title":title,"authors":authors,"year":year,"journal":journal,
                        "quartile":get_quartile(journal),"doi":doi,"pmid":None,
                        "abstract":abstract,"source":"Web of Science",
                        "access_kind":kind,"access_link":link,"scihub":scihub,
                        "oneliner":None})
        time.sleep(0.2)
    return results

# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def do_export(articles, query, fmt, synthesis=""):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w]+","_",query)[:40]
    base = Path.home() / "medsearch_exports"
    base.mkdir(parents=True, exist_ok=True)
    paths = []
    if fmt in ("md","all"):
        p = base/f"medsearch_{safe}_{ts}.md"
        lines = [f"# MedSearch Results\n\n**Query:** {query}  \n"
                 f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n"
                 f"**Articles:** {len(articles)}\n\n---\n"]
        for i,a in enumerate(articles,1):
            lines.append(f"## {i}. {a['title']}\n")
            lines.append(f"**Source:** {a['source']} | **Year:** {a['year']}  \n")
            lines.append(f"**Authors:** {a.get('authors','')}  \n")
            if a.get("doi"): lines.append(f"**DOI:** https://doi.org/{a['doi']}  \n")
            if a.get("oneliner"): lines.append(f"**Summary:** _{a['oneliner']}_  \n")
            lines.append(f"\n{a.get('abstract','')}\n\n---\n")
        if synthesis: lines.append(f"\n## AI Synthesis\n\n{synthesis}\n")
        p.write_text("".join(lines), encoding="utf-8"); paths.append(str(p))
    if fmt in ("bib","all"):
        p = base/f"medsearch_{safe}_{ts}.bib"
        entries = []
        for i,a in enumerate(articles,1):
            key = re.sub(r"[^\w]","",a.get("authors","anon").split(";")[0].split(",")[0]
                         +str(a.get("year",""))+str(i))
            df = f"  doi = {{{a['doi']}}},\n" if a.get("doi") else ""
            entries.append(f"@article{{{key},\n  title={{{a['title']}}},\n"
                           f"  author={{{a.get('authors','')}}},\n  year={{{a.get('year','')}}},\n"
                           f"  journal={{{a.get('journal','')}}},\n{df}}}")
        p.write_text("\n\n".join(entries), encoding="utf-8"); paths.append(str(p))
    if fmt in ("ris","all"):
        p = base/f"medsearch_{safe}_{ts}.ris"
        lines = []
        for a in articles:
            lines += ["TY  - JOUR",f"TI  - {a['title']}",f"AU  - {a.get('authors','')}",
                      f"PY  - {a.get('year','')}",f"JO  - {a.get('journal','')}"]
            if a.get("doi"):      lines.append(f"DO  - {a['doi']}")
            if a.get("abstract"): lines.append(f"AB  - {a['abstract'][:500]}")
            lines.append("ER  -\n")
        p.write_text("\n".join(lines), encoding="utf-8"); paths.append(str(p))
    return paths

# ══════════════════════════════════════════════════════════════════════════════
#  ZOTERO EXPORT  (via the local connector on port 23119)
# ══════════════════════════════════════════════════════════════════════════════

ZOTERO_CONNECTOR = "http://127.0.0.1:23119"

def _parse_creators(authors_str):
    """
    Turn our 'Lastname, F.; Lastname2, G.; ...' author string into Zotero's
    creators array: [{creatorType, firstName, lastName}, ...].
    Handles the ' et al.' suffix and single-field names gracefully.
    """
    creators = []
    if not authors_str:
        return creators
    cleaned = authors_str.replace(" et al.", "").strip()
    for chunk in cleaned.split(";"):
        name = chunk.strip()
        if not name:
            continue
        if "," in name:
            last, first = name.split(",", 1)
            creators.append({"creatorType":"author",
                             "firstName":first.strip(),
                             "lastName":last.strip()})
        else:
            # No comma — store as a single-field name (Zotero supports this)
            creators.append({"creatorType":"author", "name":name})
    return creators

def article_to_zotero_item(a):
    """Map one of our article dicts to a Zotero journalArticle item."""
    item = {
        "itemType":         "journalArticle",
        "title":            a.get("title",""),
        "creators":         _parse_creators(a.get("authors","")),
        "publicationTitle": a.get("journal",""),
        "date":             str(a.get("year","")),
        "abstractNote":     a.get("abstract","") or "",
        "tags":             [{"tag": "MedSearch"}],
    }
    if a.get("doi"):
        item["DOI"] = a["doi"]
        item["url"] = f"https://doi.org/{a['doi']}"
    elif a.get("pmid"):
        item["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/"
    if a.get("pmid"):
        # store PMID in the Extra field, a common convention
        item["extra"] = f"PMID: {a['pmid']}"
    return item

def zotero_ping():
    """Return True if the Zotero desktop app's connector is reachable."""
    try:
        req = urllib.request.Request(f"{ZOTERO_CONNECTOR}/connector/ping",
                                     headers={"User-Agent":"MedSearch"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

def zotero_save(articles):
    """
    POST items to the local Zotero connector's /connector/saveItems endpoint.
    Returns (ok, message). Zotero must be open with the connector available.
    """
    items = [article_to_zotero_item(a) for a in articles]
    payload = json.dumps({
        "items": items,
        "uri":   "https://medsearch.local",
        "sessionID": f"medsearch-{int(time.time())}",
    }).encode()
    req = urllib.request.Request(
        f"{ZOTERO_CONNECTOR}/connector/saveItems",
        data=payload,
        headers={"Content-Type":"application/json",
                 "User-Agent":"MedSearch",
                 "X-Zotero-Connector-API-Version":"3"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, f"{len(items)} item(s) sent to Zotero."
    except urllib.error.HTTPError as e:
        if e.code == 201:
            return True, f"{len(items)} item(s) sent to Zotero."
        body = ""
        try: body = e.read().decode()[:200]
        except Exception: pass
        return False, f"Zotero returned error {e.code}. {body}"
    except Exception as e:
        return False, f"Could not reach Zotero: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  SESSION STORE  (in-memory, per-process)
# ══════════════════════════════════════════════════════════════════════════════

SESSION = {"articles": [], "query": "", "history": [], "last_synthesis": ""}

# ══════════════════════════════════════════════════════════════════════════════
#  FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    has_key = bool((CONFIG.get("anthropic_api_key","") or "").strip())
    ai_pref = CONFIG.get("ai_enabled", True)
    # Optional deep-link from the menu-bar quick search: /?q=...&src=...
    auto_query  = (request.args.get("q") or "").strip()
    auto_source = (request.args.get("src") or CONFIG.get("default_source","pubmed") or "pubmed").strip()
    return render_template("index.html",
                           ai_on=(has_key and ai_pref),   # active only if key AND enabled
                           has_ai_key=has_key,             # whether a key exists at all
                           history=SESSION["history"][-10:],
                           saved=load_saved(),
                           show_onboarding=not CONFIG.get("onboarding_seen", False),
                           app_version=get_local_version(),
                           has_scopus=bool((CONFIG.get("scopus_api_key","") or "").strip()),
                           has_wos=bool((CONFIG.get("wos_api_key","") or "").strip()),
                           institution_proxies=CONFIG.get("institution_proxies", []),
                           active_proxy=CONFIG.get("active_proxy", 0),
                           auto_query=auto_query,
                           auto_source=auto_source)

@app.route("/ai/toggle", methods=["POST"])
def ai_toggle():
    """Turn AI features on/off (user preference, persisted)."""
    data = request.json or {}
    CONFIG["ai_enabled"] = bool(data.get("enabled", True))
    save_config(CONFIG)
    return jsonify({"ok": True, "ai_enabled": CONFIG["ai_enabled"]})

@app.route("/proxy/active", methods=["POST"])
def set_active_proxy():
    """
    Set which institution is active (persisted). The index refers to the
    frontend's merged list (predefined UniTN/ASUIT/FBK first, then customs),
    which buildInstitutions() reconstructs deterministically, so we just store
    the integer. -1 means "no library / don't proxy".
    """
    data = request.json or {}
    try:
        CONFIG["active_proxy"] = int(data.get("index", 0))
    except Exception:
        CONFIG["active_proxy"] = 0
    save_config(CONFIG)
    return jsonify({"ok": True, "active_proxy": CONFIG["active_proxy"]})

@app.route("/guidelines/bodies")
def guideline_bodies():
    """List the available national guideline bodies (for the country picker)."""
    out = [{"code": code, "name": b["name"], "country": b["country"],
            "prefill": b["prefill"]}
           for code, b in NATIONAL_GUIDELINE_BODIES.items()]
    # stable, country-name order
    out.sort(key=lambda x: x["country"])
    return jsonify({"bodies": out, "selected": CONFIG.get("guideline_country", "")})

@app.route("/guidelines/link", methods=["POST"])
def guideline_link():
    """
    Build the URL to open for a national guideline body. If the body supports a
    query parameter, the current search query is pre-filled; otherwise we return
    the portal/search URL for the user to type into. Also persists the chosen
    country so it's remembered.
    """
    data = request.json or {}
    code = (data.get("country") or "").strip().lower()
    query = (data.get("query") or "").strip()
    body = NATIONAL_GUIDELINE_BODIES.get(code)
    if not body:
        return jsonify({"ok": False, "message": "Unknown country."}), 200
    # Remember the selection
    CONFIG["guideline_country"] = code
    save_config(CONFIG)
    url = body["url"]
    prefilled = False
    if body["prefill"] and "{q}" in url:
        if query:
            url = url.replace("{q}", urllib.parse.quote(query))
            prefilled = True
        else:
            # no query to fill — strip to the bare search page
            url = url.split("?")[0]
    return jsonify({"ok": True, "url": url, "name": body["name"],
                    "country": body["country"], "prefilled": prefilled})

@app.route("/onboarding/dismiss", methods=["POST"])
def onboarding_dismiss():
    CONFIG["onboarding_seen"] = True
    save_config(CONFIG)
    return jsonify({"ok": True})

@app.route("/default_source", methods=["GET", "POST"])
def default_source():
    """Get or set the menu-bar quick-search default source (shared config)."""
    if request.method == "POST":
        data = request.json or {}
        src = (data.get("source") or "").strip() or "pubmed"
        CONFIG["default_source"] = src
        save_config(CONFIG)
        return jsonify({"ok": True, "default_source": src})
    return jsonify({"default_source": CONFIG.get("default_source", "pubmed")})

# ── Menu-bar → native-window search handoff ────────────────────────────────
# The menu-bar companion app can't reach into the running native window
# directly (separate process). Instead it POSTs a search here; the native
# window polls /pending_search and runs anything queued — so the search happens
# INSIDE the existing window, no browser hop.
_PENDING_SEARCH = {"query": None, "source": None, "ts": 0}

@app.route("/queue_search", methods=["POST"])
def queue_search():
    """Menu-bar app posts a search request to be picked up by the native window."""
    data = request.json or {}
    query = (data.get("query") or "").strip()
    source = (data.get("source") or CONFIG.get("default_source", "pubmed")).strip()
    if not query:
        return jsonify({"ok": False, "message": "Empty query."}), 200
    _PENDING_SEARCH["query"] = query
    _PENDING_SEARCH["source"] = source
    _PENDING_SEARCH["ts"] = time.time()
    return jsonify({"ok": True})

@app.route("/pending_search")
def pending_search():
    """Native window polls this; returns and clears any queued search."""
    q = _PENDING_SEARCH["query"]
    if not q:
        return jsonify({"pending": False})
    src = _PENDING_SEARCH["source"]
    # Clear it so it runs once.
    _PENDING_SEARCH["query"] = None
    _PENDING_SEARCH["source"] = None
    return jsonify({"pending": True, "query": q, "source": src})

# ── Auto-update routes ─────────────────────────────────────────────────────

@app.route("/update/check")
def update_check():
    """Compare local VERSION with the one on GitHub. No git needed for the check."""
    local = get_local_version()
    body, status = http_get(GITHUB_RAW_VERSION, timeout=8)
    if not body:
        return jsonify({"ok": False, "reason": "offline",
                        "local": local})
    remote = body.strip()
    update_available = _version_tuple(remote) > _version_tuple(local)
    # Is this a git checkout? (update can only be applied if so)
    is_git = (APP_DIR_PATH / ".git").exists()
    return jsonify({
        "ok": True,
        "local": local,
        "remote": remote,
        "update_available": update_available,
        "can_apply": is_git,
    })

@app.route("/update/apply", methods=["POST"])
def update_apply():
    """
    Update to the latest version from GitHub.
    Uses fetch + hard reset to the remote branch so local file changes
    (e.g. a flipped executable bit, or an accidental edit) can't block the
    update. User config and data live in ~/.medsearch/, outside the repo,
    so they're never touched.
    """
    if not (APP_DIR_PATH / ".git").exists():
        return jsonify({"ok": False,
                        "message": "This copy isn't a git checkout, so it can't auto-update. "
                                   "Please re-clone from GitHub."}), 200
    import subprocess
    git = ["git", "-C", str(APP_DIR_PATH)]
    version_before = get_local_version()
    try:
        # 1. Fetch the latest commits from origin
        fetch = subprocess.run(git + ["fetch", "origin"],
                               capture_output=True, text=True, timeout=60)
        if fetch.returncode != 0:
            return jsonify({"ok": False,
                            "message": "Couldn't reach GitHub to fetch the update.",
                            "error": (fetch.stderr or "").strip()[-400:]}), 200

        # 2. Determine the current branch (usually 'main')
        branch_res = subprocess.run(git + ["rev-parse", "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True, timeout=15)
        branch = (branch_res.stdout.strip() or "main")

        # 3. Hard reset to origin/<branch> — guarantees we match the remote
        reset = subprocess.run(git + ["reset", "--hard", f"origin/{branch}"],
                               capture_output=True, text=True, timeout=60)
        if reset.returncode != 0:
            return jsonify({"ok": False,
                            "message": "Update failed while applying changes.",
                            "error": (reset.stderr or reset.stdout).strip()[-400:]}), 200

        # 4. Verify the version actually changed (catch silent no-ops)
        version_after = get_local_version()
        if _version_tuple(version_after) <= _version_tuple(version_before):
            # Already at latest, or VERSION didn't move — report honestly
            return jsonify({"ok": True,
                            "new_version": version_after,
                            "unchanged": True,
                            "message": f"Already up to date (version {version_after})."})

        return jsonify({"ok": True,
                        "new_version": version_after,
                        "unchanged": False,
                        "output": reset.stdout.strip()[-300:]})

    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Update timed out."}), 200
    except FileNotFoundError:
        return jsonify({"ok": False,
                        "message": "git is not installed, so auto-update isn't available."}), 200
    except Exception as e:
        return jsonify({"ok": False, "message": f"Update error: {e}"}), 200

@app.route("/search_stream", methods=["POST"])
def search_stream():
    """Streaming search — emits results per source via SSE as they complete."""
    data    = request.json
    query   = data.get("query","").strip()
    sources = data.get("sources",[])
    max_r   = int(data.get("max_results", MAX_RESULTS_DEFAULT))
    y_from  = int(data["year_from"]) if data.get("year_from") else None
    y_to    = int(data["year_to"])   if data.get("year_to")   else None
    strict  = data.get("strict", True)   # strict by default
    sort    = data.get("sort", "relevance")   # "relevance" (default) or "date"
    offset  = int(data.get("offset", 0))      # >0 = "load more" (next batch)
    if sort not in ("relevance", "date"):
        sort = "relevance"

    if not query:
        return Response("data: "+json.dumps({"type":"error","text":"Empty query"})+"\n\n",
                        mimetype="text/event-stream")

    # Fresh search resets the session; "load more" (offset>0) keeps existing
    # results and appends to them.
    if offset == 0:
        SESSION["articles"] = []
        SESSION["query"]    = query
        SESSION["last_synthesis"] = ""
        if query not in SESSION["history"]: SESSION["history"].append(query)

    # Ordered list of (key, label, callable) to run
    def make_runners(seen):
        runners = []
        # Cochrane first: systematic reviews are also in PubMed, so claiming them
        # here (before general PubMed) labels them as Cochrane in the dedup.
        if "cochrane" in sources or "all" in sources:
            runners.append(("cochrane", "Cochrane",
                            lambda: search_cochrane(query, max_r, y_from, y_to, seen, strict=strict, sort=sort, offset=offset)))
        # Clinical practice guidelines: PubMed restricted to guideline publication
        # types. Captures national/society guidelines from many countries indexed
        # in PubMed (incl. Italy's SNLG, US, UK, etc.). Runs BEFORE plain PubMed so
        # guideline papers are claimed and labelled "Guidelines" (same pattern as
        # Cochrane claiming systematic reviews first).
        if "guidelines" in sources or "all" in sources:
            runners.append(("guidelines", "Guidelines",
                            lambda: search_pubmed(
                                query, max_r, y_from, y_to, seen, strict=strict, sort=sort,
                                extra_filter="(Guideline[ptyp] OR \"Practice Guideline\"[ptyp])",
                                source_label="Guidelines", offset=offset)))
        if "pubmed" in sources or "all" in sources:
            runners.append(("pubmed", "PubMed",
                            lambda: search_pubmed(query, max_r, y_from, y_to, seen, strict=strict, sort=sort, offset=offset)))
        if "scopus" in sources or "all" in sources:
            runners.append(("scopus", "Scopus",
                            lambda: (search_scopus(query, max_r, y_from, y_to, seen, sort=sort, offset=offset), 0)))
        if "wos" in sources or "all" in sources:
            runners.append(("wos", "Web of Science",
                            lambda: (search_wos(query, max_r, y_from, y_to, seen, sort=sort, offset=offset), 0)))
        if "clinicaltrials" in sources or "all" in sources:
            runners.append(("clinicaltrials", "ClinicalTrials.gov",
                            lambda: (search_clinicaltrials(query, max_r, y_from, y_to, seen, sort=sort, offset=offset), 0)))
        if "arxiv" in sources or "all" in sources:
            runners.append(("arxiv", "arXiv",
                            lambda: (search_arxiv(query, max_r, y_from, y_to, seen, sort=sort, offset=offset), 0)))
        return runners

    def generate():
        seen = make_dedup_set()
        # For "load more", continue from existing results: rebuild the dedup set
        # from what's already shown (so new results don't repeat them) and keep
        # the running index/list going.
        if offset > 0 and SESSION.get("articles"):
            all_results = list(SESSION["articles"])
            global_idx = len(all_results)
            for a in all_results:
                register(seen, a.get("doi"), a.get("title"))
        else:
            all_results = []
            global_idx = 0
        ai_on = bool((CONFIG.get("anthropic_api_key","") or "").strip()) and CONFIG.get("ai_enabled", True)

        # Send an initial padding comment to defeat buffering in some webviews.
        yield ":" + (" " * 2048) + "\n\n"

        # 1. MeSH first (fast, gives the user something immediately) — only on a
        # fresh search, not when loading more.
        if offset == 0 and ("pubmed" in sources or "all" in sources):
            mesh = get_mesh(query)
            yield "data: " + json.dumps({"type":"mesh","mesh":mesh}) + "\n\n"

        runners = make_runners(seen)
        total_sources = len(runners)

        # Each source: announce start → emit each article card immediately →
        # then stream one-liners as they finish (cards fill in live).
        for i, (key, label, fn) in enumerate(runners, 1):
            yield "data: " + json.dumps({
                "type":"source_start", "source":label,
                "index":i, "total":total_sources
            }) + "\n\n"
            yield ":keep-alive\n\n"

            # Premium sources need an API key. If enabled without one, tell the
            # user clearly instead of silently returning nothing.
            _key_required = {
                "scopus": ("scopus_api_key", "Scopus"),
                "wos":    ("wos_api_key", "Web of Science"),
            }
            if key in _key_required:
                cfg_field, nice = _key_required[key]
                if not (CONFIG.get(cfg_field,"") or "").strip():
                    yield "data: " + json.dumps({
                        "type":"source_error", "source":label,
                        "text":f"Add a {nice} API key in Settings to search this source."
                    }) + "\n\n"
                    yield "data: " + json.dumps({
                        "type":"source_done", "source":label,
                        "count":0, "total_pubmed":0, "running_count":len(all_results)
                    }) + "\n\n"
                    continue

            try:
                r = fn()
                res = r[0] if isinstance(r, tuple) else r
                total_pubmed = r[1] if (isinstance(r, tuple) and key=="pubmed") else 0
            except Exception as e:
                res = []; total_pubmed = 0
                yield "data: " + json.dumps({"type":"source_error","source":label,"text":str(e)}) + "\n\n"

            # Assign global indices and emit each card right away (no one-liner yet)
            indexed = []
            for a in res:
                a["_idx"] = global_idx
                indexed.append(a)
                all_results.append(a)
                global_idx += 1
                yield "data: " + json.dumps({"type":"article","source":label,"article":a}) + "\n\n"
            SESSION["articles"] = all_results

            # Tell the client this source is done arriving (spinner → count)
            yield "data: " + json.dumps({
                "type":"source_done", "source":label,
                "count":len(res), "total_pubmed":total_pubmed,
                "running_count":len(all_results)
            }) + "\n\n"
            yield ":keep-alive\n\n"

            # Now stream one-liners as they complete, patching each card live.
            if ai_on:
                targets = [a for a in indexed if a.get("abstract") and not a.get("oneliner")]
                if targets:
                    try:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                            future_to_idx = {
                                ex.submit(ai_oneliner, a.get("title",""), a.get("abstract","")): a["_idx"]
                                for a in targets
                            }
                            for fut in concurrent.futures.as_completed(future_to_idx):
                                idx = future_to_idx[fut]
                                try: ol = fut.result()
                                except Exception: ol = None
                                if ol:
                                    # update session copy too
                                    for a in all_results:
                                        if a.get("_idx") == idx: a["oneliner"] = ol; break
                                    yield "data: " + json.dumps({
                                        "type":"oneliner","idx":idx,"text":ol
                                    }) + "\n\n"
                    except Exception:
                        pass

        # Final done event
        yield "data: " + json.dumps({"type":"done","count":len(all_results)}) + "\n\n"

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "keep-alive"
    resp.headers["Content-Encoding"] = "none"   # prevent gzip buffering
    return resp

@app.route("/synthesis")
def synthesis():
    query    = SESSION.get("query","")
    articles = SESSION.get("articles",[])
    def generate():
        text = ""
        for chunk in ai_synthesis_stream(query, articles):
            if '"type":"chunk"' in chunk:
                try: text += json.loads(chunk[6:])["text"]
                except Exception: pass
            yield chunk
        SESSION["last_synthesis"] = text
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/explain/<int:idx>")
def explain(idx):
    articles = SESSION.get("articles",[])
    if idx < 0 or idx >= len(articles):
        return Response("data: "+json.dumps({"type":"error","text":"Invalid index"})+"\n\n",
                        mimetype="text/event-stream")
    return Response(stream_with_context(ai_explain_stream(articles[idx])),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/citations/<int:idx>")
def citations(idx):
    articles = SESSION.get("articles",[])
    if idx < 0 or idx >= len(articles):
        return jsonify({"error":"Invalid index"}), 400
    art = articles[idx]
    doi = art.get("doi")
    if not doi:
        return jsonify({"error":"no_doi",
                        "message":"This article has no DOI, so its citation graph can't be retrieved.",
                        "title": art.get("title","")}), 200
    graph = get_citation_graph(doi)
    graph["source_title"] = art.get("title","")
    graph["source_year"]  = art.get("year","")
    return jsonify(graph)

# ── AI Assistant routes ────────────────────────────────────────────────────

@app.route("/assistant/suggestions")
def assistant_suggestions_route():
    """Return suggested follow-up questions for the current search."""
    query    = SESSION.get("query","")
    articles = SESSION.get("articles",[])
    return jsonify({"suggestions": assistant_suggestions(query, articles)})

@app.route("/assistant/chat", methods=["POST"])
def assistant_chat_route():
    """Streaming chat grounded in the current results.
    Body: {messages: [{role, content}, ...]}"""
    data     = request.json or {}
    messages = data.get("messages", [])
    # Basic validation/sanitation of the conversation
    clean = []
    for m in messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user","assistant") and content:
            clean.append({"role":role, "content":content[:4000]})
    if not clean or clean[-1]["role"] != "user":
        return Response("data: "+json.dumps({"type":"error","text":"No question provided."})+"\n\n",
                        mimetype="text/event-stream")
    # Keep only the last ~6 turns to bound the prompt size (was 12)
    clean = clean[-6:]
    query    = SESSION.get("query","")
    articles = SESSION.get("articles",[])
    return Response(stream_with_context(assistant_chat_stream(clean, query, articles)),
                    mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

def _is_scihub_url(url):
    """True if the URL points at a known Sci-Hub mirror."""
    mirrors = CONFIG.get("scihub_mirrors") or DEFAULTS["scihub_mirrors"]
    hosts = []
    for m in mirrors:
        try: hosts.append(urllib.parse.urlparse(m).hostname or "")
        except Exception: pass
    try:
        h = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    return any(h == host or h.endswith("." + host) for host in hosts if host)

def _abs_url(href, page_url):
    """Resolve a possibly-relative/protocol-relative href against page_url."""
    if not href:
        return None
    href = href.strip().split("#")[0]
    if not href:
        return None
    if href.startswith("//"):
        scheme = urllib.parse.urlparse(page_url).scheme or "https"
        return f"{scheme}:{href}"
    if href.startswith("/"):
        base = urllib.parse.urlparse(page_url)
        return f"{base.scheme}://{base.netloc}{href}"
    if not href.startswith(("http://", "https://")):
        return urllib.parse.urljoin(page_url, href)
    return href

def _extract_pdf_url_from_landing(html_text, page_url):
    """
    Many publisher/repository 'open access' links point at an HTML landing page
    rather than a direct PDF. Most academic pages advertise the real PDF via a
    <meta name="citation_pdf_url"> tag (Google Scholar convention); some embed it
    in <iframe>/<embed> or link it with a .pdf href. Return the best PDF URL or None.
    """
    # 1. citation_pdf_url meta tag — the most reliable signal across publishers
    m = re.search(
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        html_text, flags=re.IGNORECASE)
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
            html_text, flags=re.IGNORECASE)
    if m:
        u = _abs_url(m.group(1), page_url)
        if u:
            return u
    # 2. iframe/embed/href pointing at something PDF-ish
    candidates = []
    for pat in [
        r'<iframe[^>]+src\s*=\s*["\']([^"\']+)["\']',
        r'<embed[^>]+src\s*=\s*["\']([^"\']+)["\']',
        r'href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']',
    ]:
        candidates += re.findall(pat, html_text, flags=re.IGNORECASE)
    for c in candidates:
        u = _abs_url(c, page_url)
        if u and (".pdf" in u.lower() or "/pdf" in u.lower()):
            return u
    return None

def _extract_scihub_pdf_url(html_text, page_url):
    """
    Sci-Hub returns an HTML page with the actual PDF embedded in an <iframe>,
    <embed>, or a download button. Parse out that real PDF URL and return it
    absolute, or None if not found.
    """
    candidates = []
    # Common Sci-Hub patterns: <iframe src="..."> / <embed src="..."> /
    # onclick="location.href='...'" download button.
    for pat in [
        r'<iframe[^>]+src\s*=\s*["\']([^"\']+)["\']',
        r'<embed[^>]+src\s*=\s*["\']([^"\']+)["\']',
        r'location\.href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']',
        r'href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']',
    ]:
        candidates += re.findall(pat, html_text, flags=re.IGNORECASE)

    for c in candidates:
        u = _abs_url(c, page_url)
        if not u:
            continue
        low = u.lower()
        if ".pdf" in low or "/pdf" in low or "downloads" in low:
            return u
    # Fallback: if exactly one iframe/embed was found, use it even without .pdf
    if candidates:
        return _abs_url(candidates[0], page_url)
    return None


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/pdf,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

def _fetch_url_bytes(url, timeout=30, referer=None):
    """
    Fetch a URL with browser-like headers; return (data_bytes, content_type).
    Handles gzip/deflate and follows redirects (urllib does this, but we add a
    Referer of the page's own origin which some publishers require).
    Raises urllib.error.HTTPError on 4xx/5xx so callers can react (e.g. 403).
    """
    import gzip, zlib
    headers = dict(_BROWSER_HEADERS)
    # A same-origin Referer placates some publishers' hotlink protection.
    parsed = urllib.parse.urlparse(url)
    headers["Referer"] = referer or f"{parsed.scheme}://{parsed.netloc}/"
    req = urllib.request.Request(url, headers=headers)
    upstream = urllib.request.urlopen(req, timeout=timeout)
    raw = upstream.read()
    enc = (upstream.headers.get("Content-Encoding", "") or "").lower()
    try:
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            try: raw = zlib.decompress(raw)
            except Exception: raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        pass
    ctype = upstream.headers.get("Content-Type", "").lower()
    return raw, ctype

def _scihub_urls_for(url):
    """
    Given a primary access URL that's failing, find the Sci-Hub mirror URLs for
    the SAME article (matched via the session by access_link), so we can fall
    through to Sci-Hub automatically. Returns a list (possibly empty).
    """
    for a in SESSION.get("articles", []):
        if a.get("access_link") == url:
            return list(a.get("scihub") or [])
    return []

def _try_scihub_chain(mirrors):
    """
    Try each Sci-Hub mirror in turn: fetch the page, extract the embedded PDF,
    fetch that, and return (pdf_bytes, ctype) on the first success, else None.
    """
    for m in mirrors:
        try:
            data, ctype = _fetch_url_bytes(m)
            if ("pdf" in ctype) or data[:5] == b"%PDF-":
                return data, ctype
            html_text = data.decode("utf-8", errors="replace")
            pdf_url = _extract_scihub_pdf_url(html_text, m)
            if pdf_url:
                pdata, pctype = _fetch_url_bytes(pdf_url, referer=m)
                if ("pdf" in pctype) or pdata[:5] == b"%PDF-":
                    return pdata, pctype
        except Exception:
            continue   # try the next mirror
    return None

@app.route("/pdf_proxy")
def pdf_proxy():
    """
    Fetch a PDF server-side and stream it to the client. Bypasses
    X-Frame-Options / CORS that block embedding external PDFs in an iframe.
    Only proxies links the app already surfaced (not an open relay).

    For Sci-Hub URLs, which return an HTML viewer page rather than a direct
    PDF, we parse out the embedded PDF URL and fetch that instead.
    """
    url = request.args.get("url","").strip()
    if not url or not url.lower().startswith(("http://","https://")):
        return jsonify({"error":"Invalid URL"}), 400

    # Light safety: only proxy if this URL relates to the current session's
    # known access links (prevents the proxy being used as an open relay).
    # We also accept institutional-proxy *variants* of those known URLs, since
    # the DOI button may rewrite the host (ezp.biblio.unitn.it) or wrap it as
    # ?url=<encoded>. We match by checking whether a known DOI/host appears.
    known = set()
    known_dois = set()
    for a in SESSION.get("articles", []):
        if a.get("access_link"): known.add(a["access_link"])
        for m in (a.get("scihub") or []): known.add(m)
        if a.get("doi"): known_dois.add(str(a["doi"]).lower())

    def _is_allowed(u):
        if u in known:
            return True
        low = urllib.parse.unquote(u).lower()
        # Proxied form embeds the original DOI somewhere in the URL
        for d in known_dois:
            if d and d in low:
                return True
        # Proxied ?url= form embeds a known access link
        for k in known:
            if k and k.lower() in low:
                return True
        return False

    if not _is_allowed(url):
        return jsonify({"error":"URL not recognized from current results"}), 403

    try:
        # ── Attempt 1: the requested URL directly ──────────────────────────
        primary_error = None
        data = ctype = None
        is_pdf = False
        try:
            data, ctype = _fetch_url_bytes(url)
            is_pdf = ("pdf" in ctype) or data[:5] == b"%PDF-"
        except urllib.error.HTTPError as e:
            primary_error = e.code            # e.g. 403 from a publisher
        except Exception:
            primary_error = "fetch"

        # ── If it's a Sci-Hub URL serving HTML, extract the embedded PDF ────
        if data is not None and not is_pdf and _is_scihub_url(url):
            html_text = data.decode("utf-8", errors="replace")
            pdf_url = _extract_scihub_pdf_url(html_text, url)
            if pdf_url:
                try:
                    data, ctype = _fetch_url_bytes(pdf_url, referer=url)
                    is_pdf = ("pdf" in ctype) or data[:5] == b"%PDF-"
                except Exception:
                    pass

        # ── If it's a publisher landing page (HTML), look for the real PDF ──
        elif data is not None and not is_pdf and "html" in (ctype or ""):
            html_text = data.decode("utf-8", errors="replace")
            pdf_url = _extract_pdf_url_from_landing(html_text, url)
            if pdf_url and pdf_url != url:
                try:
                    data, ctype = _fetch_url_bytes(pdf_url, referer=url)
                    is_pdf = ("pdf" in ctype) or data[:5] == b"%PDF-"
                except Exception:
                    pass

        # ── Fallthrough: primary route failed (403/paywall/no-PDF). If this
        #    article has Sci-Hub mirrors, try them automatically before giving
        #    up — Sci-Hub serves the PDF directly and isn't IP/paywall-gated. ─
        if not is_pdf and not _is_scihub_url(url):
            mirrors = _scihub_urls_for(url)
            if mirrors:
                got = _try_scihub_chain(mirrors)
                if got:
                    data, ctype = got
                    is_pdf = True

        # ── Success ────────────────────────────────────────────────────────
        if is_pdf and data:
            resp = Response(data, mimetype="application/pdf")
            resp.headers["Content-Disposition"] = "inline; filename=article.pdf"
            resp.headers["Cache-Control"] = "private, max-age=600"
            return resp

        # ── Honest, specific failure messages ──────────────────────────────
        if _is_scihub_url(url):
            return jsonify({"error":"scihub_no_pdf",
                            "message":"Sci-Hub doesn't have a readable PDF for this article "
                                      "(it may not be in their collection)."}), 415
        if primary_error == 403:
            return jsonify({"error":"forbidden",
                            "message":"The publisher blocked the download (403). This is common for "
                                      "paywalled journals. Try the Sci-Hub button, or open it in your browser "
                                      "where your institutional login applies."}), 415
        if primary_error:
            return jsonify({"error":"fetch_failed",
                            "message":"Couldn't reach this PDF directly. Try the Sci-Hub button, or open "
                                      "it in your browser."}), 502
        return jsonify({"error":"not_pdf",
                        "message":"This link opens a web page rather than a direct PDF. Try the Sci-Hub "
                                  "button, or open it in your browser."}), 415
    except Exception as e:
        return jsonify({"error":"fetch_failed",
                        "message":f"Couldn't fetch the PDF: {e}"}), 502

@app.route("/export", methods=["POST"])
def export():
    data      = request.json
    fmt       = data.get("format","md")
    synthesis = SESSION.get("last_synthesis","")
    articles  = SESSION.get("articles",[])
    query     = SESSION.get("query","")
    if not articles: return jsonify({"error":"No articles to export"}), 400
    paths = do_export(articles, query, fmt, synthesis)
    return jsonify({"paths": paths})

@app.route("/export/zotero/check")
def export_zotero_check():
    """Tell the frontend whether Zotero's connector is reachable right now."""
    return jsonify({"available": zotero_ping()})

@app.route("/export/zotero", methods=["POST"])
def export_zotero():
    articles = SESSION.get("articles",[])
    if not articles:
        return jsonify({"ok": False, "message": "No articles to send."}), 200
    if not zotero_ping():
        return jsonify({"ok": False, "available": False,
                        "message": "Zotero isn't running. Open the Zotero desktop app and try again."}), 200
    ok, msg = zotero_save(articles)
    return jsonify({"ok": ok, "available": True, "message": msg})

@app.route("/export/zotero/single", methods=["POST"])
def export_zotero_single():
    """Send one article (by its session index) straight to Zotero."""
    data = request.json or {}
    try:
        idx = int(data.get("idx", -1))
    except Exception:
        idx = -1
    articles = SESSION.get("articles", [])
    if idx < 0 or idx >= len(articles) or articles[idx] is None:
        return jsonify({"ok": False, "message": "Article not found."}), 200
    if not zotero_ping():
        return jsonify({"ok": False, "available": False,
                        "message": "Zotero isn't running. Open the Zotero desktop app and try again."}), 200
    ok, msg = zotero_save([articles[idx]])
    return jsonify({"ok": ok, "available": True, "message": msg})

@app.route("/settings", methods=["GET","POST"])
def settings():
    global CONFIG
    if request.method == "POST":
        data = request.json
        for k in ("anthropic_api_key","pubmed_api_key","scopus_api_key",
                  "scopus_insttoken","wos_api_key","unpaywall_email"):
            if k in data and data[k]: CONFIG[k] = data[k].strip()
        # Institutional proxies: list of {label, url}; active index.
        if "institution_proxies" in data and isinstance(data["institution_proxies"], list):
            cleaned = []
            for p in data["institution_proxies"]:
                if not isinstance(p, dict):
                    continue
                url = (p.get("url") or "").strip()
                label = (p.get("label") or "").strip() or "Institution"
                if url:
                    entry = {"label": label, "url": url}
                    # Preserve the stable id for predefined institutions so the
                    # URL rehydrates into the right slot (UniTN/ASUIT/FBK) on load.
                    if p.get("id"):
                        entry["id"] = str(p["id"])
                    cleaned.append(entry)
            CONFIG["institution_proxies"] = cleaned
            # keep the legacy single field in sync with the first entry
            CONFIG["institution_proxy"] = cleaned[0]["url"] if cleaned else ""
        if "active_proxy" in data:
            try:
                # -1 is a valid value meaning "no library / don't proxy"
                CONFIG["active_proxy"] = int(data["active_proxy"])
            except Exception:
                CONFIG["active_proxy"] = 0
        save_config(CONFIG)
        return jsonify({"ok": True})
    # Mask API keys (show only last 4 chars). Email and proxy URL aren't
    # sensitive, so return them in full so the user can see and verify them.
    safe = {}
    for k, v in CONFIG.items():
        if not isinstance(v, str):
            continue
        if k in ("unpaywall_email", "institution_proxy"):
            safe[k] = v
        elif len(v) > 4:
            safe[k] = "*"*(len(v)-4) + v[-4:]
        else:
            safe[k] = "set" if v else ""
    # Non-string settings the UI needs back in full
    safe["institution_proxies"] = CONFIG.get("institution_proxies", [])
    safe["active_proxy"] = CONFIG.get("active_proxy", 0)
    return jsonify(safe)

@app.route("/history")
def history():
    return jsonify(SESSION["history"][-20:])

@app.route("/history/delete", methods=["POST"])
def history_delete():
    q = (request.json or {}).get("query","")
    SESSION["history"] = [h for h in SESSION["history"] if h != q]
    return jsonify({"ok": True, "history": SESSION["history"][-20:]})

@app.route("/history/clear", methods=["POST"])
def history_clear():
    SESSION["history"] = []
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  SAVED SEARCHES  (persisted to disk so they survive restarts)
# ══════════════════════════════════════════════════════════════════════════════

SAVED_FILE = CONFIG_DIR / "saved_searches.json"

def load_saved():
    if SAVED_FILE.exists():
        try: return json.loads(SAVED_FILE.read_text())
        except Exception: return []
    return []

def write_saved(items):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SAVED_FILE.write_text(json.dumps(items, indent=2))

@app.route("/saved", methods=["GET"])
def saved_list():
    return jsonify(load_saved())

@app.route("/saved", methods=["POST"])
def saved_add():
    data  = request.json
    items = load_saved()
    entry = {
        "id":        str(int(time.time()*1000)),
        "name":      data.get("name","").strip() or data.get("query","Untitled"),
        "query":     data.get("query",""),
        "sources":   data.get("sources",[]),
        "year_from": data.get("year_from"),
        "year_to":   data.get("year_to"),
        "max_results": data.get("max_results", MAX_RESULTS_DEFAULT),
        "strict":    data.get("strict", True),
        "created":   datetime.now().strftime("%Y-%m-%d"),
    }
    # avoid exact duplicates (same name + query)
    if not any(s["name"] == entry["name"] and s["query"] == entry["query"] for s in items):
        items.append(entry)
        write_saved(items)
    return jsonify({"ok": True, "saved": items})

@app.route("/saved/clear", methods=["POST"])
def saved_clear():
    write_saved([])
    return jsonify({"ok": True, "saved": []})

@app.route("/saved/<sid>", methods=["DELETE"])
def saved_delete(sid):
    items = [s for s in load_saved() if s["id"] != sid]
    write_saved(items)
    return jsonify({"ok": True, "saved": items})

if __name__ == "__main__":
    import threading, socket, argparse

    # Optional deep-link args from the menu-bar app: open the window straight on
    # a search. e.g.  python3 app.py --query "glioma" --source guidelines
    _parser = argparse.ArgumentParser(add_help=False)
    _parser.add_argument("--query", default="")
    _parser.add_argument("--source", default="")
    _args, _ = _parser.parse_known_args()

    # Find a free port (in case 5050 is taken)
    def free_port(preferred=5050):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", preferred)); s.close(); return preferred
        except OSError:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s2.bind(("127.0.0.1", 0)); port = s2.getsockname()[1]; s2.close(); return port

    PORT = free_port(5050)
    # If launched with a query, point the window straight at the search.
    if _args.query.strip():
        _qs = urllib.parse.urlencode({"q": _args.query.strip(),
                                      "src": (_args.source or "").strip()})
        URL = f"http://127.0.0.1:{PORT}/?{_qs}"
    else:
        URL = f"http://127.0.0.1:{PORT}"

    # Record the chosen port so the menu-bar companion app can find this server
    # (it may be on a non-default port if 5050 was taken). Best-effort.
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "server_port").write_text(str(PORT))
    except Exception:
        pass

    def run_server():
        app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True, use_reloader=False)

    # Try to open a native window via pywebview; fall back to a browser tab.
    try:
        import webview  # pywebview

        # JS-callable API: lets the page open a real in-app browser window for
        # any URL the server can't fetch directly (publisher pages, paywalled
        # PDFs via the library proxy, JS-rendered viewers). This window is a
        # full browser — it runs JavaScript and carries the user's login
        # session/cookies, so institutional access and paywalls just work.
        class Api:
            def open_external(self, url, title=None):
                try:
                    if not url or not str(url).lower().startswith(("http://", "https://")):
                        return {"ok": False, "error": "bad url"}
                    webview.create_window(
                        title or "MedSearch — Article",
                        url,
                        width=1100, height=860,
                        min_size=(800, 600),
                    )
                    return {"ok": True}
                except Exception as e:
                    return {"ok": False, "error": str(e)}

        api = Api()
        # Start Flask in a background thread
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        print(f"\n  🔬  MedSearch {LOCAL_VERSION}  —  native window on {URL}\n")
        webview.create_window(
            "MedSearch",
            URL,
            width=1280, height=860,
            min_size=(940, 640),
            js_api=api,
        )
        # Persist the embedded browser's cookies and session so an institutional
        # login carries across every article window AND survives app restarts —
        # log in once, not for every paper. By default pywebview runs in private
        # mode (no cookies saved), which is why each window asked to log in again.
        # If this pywebview build doesn't support these options, fall back to a
        # plain start so the app still launches (just without persisted login).
        try:
            browser_data_dir = str(CONFIG_DIR / "browser_data")
            os.makedirs(browser_data_dir, exist_ok=True)
            webview.start(private_mode=False, storage_path=browser_data_dir)
        except TypeError:
            webview.start()
    except ImportError:
        import webbrowser
        print(f"\n  🔬  MedSearch {LOCAL_VERSION}  —  starting…")
        print(f"  (pywebview not installed — opening in browser instead)")
        print(f"  Open: {URL}\n")
        threading.Timer(1.2, lambda: webbrowser.open(URL)).start()
        run_server()
