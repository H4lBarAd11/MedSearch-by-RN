#!/usr/bin/env python3
"""
MedSearch v4.0 — Flask Web GUI
Author: Riccardo Nevoso

Wraps all search logic from medsearch.py and serves a browser-based interface.
Results and AI text stream live via Server-Sent Events (SSE).
"""

import sys, os, json, re, time, threading, queue, urllib.parse, urllib.request
import urllib.error, xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, Response, jsonify, stream_with_context

# ── resolve path so app.py can be run from anywhere ──────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
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
    "wos_api_key":       "",
    "unpaywall_email":   "",
    "scihub_mirrors":    ["https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru"],
}

def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try: cfg.update(json.loads(CONFIG_FILE.read_text()))
        except: pass
    for k, e in {"anthropic_api_key":"ANTHROPIC_API_KEY","pubmed_api_key":"NCBI_API_KEY",
                 "scopus_api_key":"SCOPUS_API_KEY","wos_api_key":"WOS_API_KEY",
                 "unpaywall_email":"UNPAYWALL_EMAIL"}.items():
        v = os.environ.get(e,"")
        if v: cfg[k] = v
    return cfg

def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

CONFIG = load_config()

TIMEOUT = 15
MAX_RESULTS_DEFAULT = 10

# ── Auto-update configuration ──────────────────────────────────────────────
APP_DIR_PATH    = Path(__file__).parent.resolve()
VERSION_FILE    = APP_DIR_PATH / "VERSION"
# Raw GitHub URL for the VERSION file on the main branch
GITHUB_RAW_VERSION = "https://raw.githubusercontent.com/H4lBarAd11/MedSearch-by-RN/main/VERSION"

def get_local_version():
    try:
        return VERSION_FILE.read_text().strip()
    except Exception:
        return "Beta 0"

def _version_tuple(v):
    """
    Parse a version string into a comparable tuple.
    Handles 'Beta 5' (→ (5,)) and dotted 'x.y.z' (→ (x,y,z)).
    Any embedded number is extracted; missing/garbage → 0.
    """
    s = str(v).strip()
    # Pull all integers out of the string, in order.
    nums = re.findall(r"\d+", s)
    if not nums:
        return (0,)
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
        "User-Agent": "MedSearch/4.0 (academic research; Riccardo Nevoso)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), r.status
    except urllib.error.HTTPError as e: return None, e.code
    except: return None, 0

def fetch_json(url, headers=None):
    body, status = http_get(url, headers)
    if body:
        try: return json.loads(body), status
        except: pass
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

def check_oa(doi):
    if not doi: return None
    email = CONFIG.get("unpaywall_email") or "research@example.com"
    data, _ = fetch_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={email}")
    if data and data.get("is_oa"):
        loc = data.get("best_oa_location") or {}
        return loc.get("url_for_pdf") or loc.get("url")
    return None

def resolve_access(doi):
    oa = check_oa(doi)
    if oa:  return "open", oa
    if doi: return "doi",  f"https://doi.org/{doi}"
    return "none", None

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

def add_oneliners_parallel(articles, max_workers=8):
    """
    Generate AI one-liners for a batch of articles concurrently and patch them
    into each dict in-place. Much faster than sequential calls: a source with
    10 articles finishes in ~1 call's time instead of 10. No-op if AI is off.
    """
    if not (CONFIG.get("anthropic_api_key","") or "").strip():
        return articles
    targets = [a for a in articles if a.get("abstract") and not a.get("oneliner")]
    if not targets:
        return articles
    def _one(a):
        a["oneliner"] = ai_oneliner(a.get("title",""), a.get("abstract",""))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(_one, targets))
    except Exception:
        pass   # if the pool fails, articles just have no one-liners
    return articles

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
                except: continue
    except urllib.error.HTTPError as e:
        try: detail = e.read().decode()
        except: detail = ""
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
                except: continue
    except Exception as e:
        yield "data: "+json.dumps({"type":"error","text":str(e)})+"\n\n"
    yield "data: "+json.dumps({"type":"done"})+"\n\n"

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
                except: pass
    return suggestions

# ══════════════════════════════════════════════════════════════════════════════
#  CITATION GRAPH  (OpenCitations COCI API + Crossref title resolution)
# ══════════════════════════════════════════════════════════════════════════════

import concurrent.futures

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
    except: return True

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
                  extra_filter=None, source_label="PubMed"):
    results = []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    kp   = f"&api_key={CONFIG['pubmed_api_key']}" if CONFIG.get("pubmed_api_key") else ""
    dp   = (f"&mindate={y_from or 1900}/01/01&maxdate={y_to or 2099}/12/31&datetype=pdat"
            if y_from or y_to else "")
    term = build_pubmed_term(query, strict=strict)
    if extra_filter:
        term = f"({term}) AND {extra_filter}"
    # sort=relevance → PubMed "Best Match" ranking (same as the website default)
    esearch = (f"{base}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(term)}"
               f"&retmax={max_r}&sort=relevance&retmode=json{kp}{dp}")
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
        kind, link = resolve_access(doi)
        scihub = f"{CONFIG['scihub_mirrors'][0]}/{doi}" if doi else None
        results.append({"title":title,"authors":authors,"year":year,"journal":journal,
                        "quartile":get_quartile(journal),"doi":doi,"pmid":pmid,
                        "abstract":abstract,"source":source_label,"access_kind":kind,
                        "access_link":link,"scihub":scihub,
                        "oneliner":None})
        time.sleep(0.12)
    return results, total

def search_cochrane(query, max_r, y_from, y_to, seen, strict=True):
    """
    Cochrane systematic reviews are indexed in PubMed under the journal
    'Cochrane Database of Systematic Reviews'. We search PubMed restricted to
    that journal, giving real inline results instead of a dead external link.
    """
    # [ta] = journal title abbreviation field; covers the current journal name.
    cochrane_filter = '"Cochrane Database Syst Rev"[ta]'
    res, total = search_pubmed(query, max_r, y_from, y_to, seen,
                               strict=strict, extra_filter=cochrane_filter,
                               source_label="Cochrane")
    return res, total

def search_arxiv(query, max_r, y_from, y_to, seen):
    results = []
    body, _ = http_get(f"https://export.arxiv.org/api/query?search_query=all:"
                       f"{urllib.parse.quote(query)}&max_results={max_r}&sortBy=relevance")
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

def search_clinicaltrials(query, max_r, y_from, y_to, seen):
    results = []
    data, _ = fetch_json(f"https://clinicaltrials.gov/api/v2/studies"
                         f"?query.term={urllib.parse.quote(query)}&pageSize={max_r}&format=json")
    if not data: return results
    for study in data.get("studies",[]):
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

def search_biorxiv(query, server, max_r, y_from, y_to, seen):
    results = []
    label = "medRxiv" if server=="medrxiv" else "bioRxiv"
    data, _ = fetch_json(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                         f"?db=pmc&term={urllib.parse.quote(query)}+AND+{server}[filter]"
                         f"&retmax={max_r}&retmode=json")
    if not data: return results
    ids = data.get("esearchresult",{}).get("idlist",[])
    if not ids: return results
    body, _ = http_get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                       f"?db=pmc&id={','.join(ids)}&retmode=xml")
    if not body: return results
    try:
        root = ET.fromstring(body)
        for art in root.findall(".//article"):
            te = art.find(".//article-title")
            title = "".join(te.itertext()).strip() if te is not None else "No title"
            ae = art.find(".//abstract")
            abstract = "".join(ae.itertext()).strip() if ae is not None else ""
            ye = art.find(".//pub-date/year")
            year = ye.text if ye is not None else "n.d."
            if not within_range(year, y_from, y_to): continue
            de = art.find(".//article-id[@pub-id-type='doi']")
            doi = de.text if de is not None else None
            if is_duplicate(seen, doi, title): continue
            register(seen, doi, title)
            kind, link = resolve_access(doi)
            results.append({"title":title,"authors":"See preprint","year":year,
                            "journal":label,"quartile":None,"doi":doi,"pmid":None,
                            "abstract":abstract,"source":label,"access_kind":"open",
                            "access_link":link or f"https://www.{server}.org","scihub":None,
                            "oneliner":None})
    except: pass
    return results

def search_scopus(query, max_r, y_from, y_to, seen):
    results = []
    key = CONFIG.get("scopus_api_key","")
    if not key: return results
    dr = (f" AND PUBYEAR > {(y_from or 1900)-1} AND PUBYEAR < {(y_to or 2099)+1}"
          if y_from or y_to else "")
    data, status = fetch_json(
        f"https://api.elsevier.com/content/search/scopus"
        f"?query={urllib.parse.quote(query+dr)}&count={max_r}"
        f"&apiKey={key}&httpAccept=application%2Fjson",
        headers={"X-ELS-APIKey":key,"Accept":"application/json"})
    if not data or status!=200: return results
    for e in data.get("search-results",{}).get("entry",[]):
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
        scihub = f"{CONFIG['scihub_mirrors'][0]}/{doi}" if doi else None
        results.append({"title":title,"authors":creator,"year":year,"journal":pub,
                        "quartile":get_quartile(pub),"doi":doi,"pmid":None,
                        "cited_by":cited,"abstract":abstract,"source":"Scopus",
                        "access_kind":kind,"access_link":link,"scihub":scihub,
                        "oneliner":None})
        time.sleep(0.2)
    return results

def search_wos(query, max_r, y_from, y_to, seen):
    results = []
    key = CONFIG.get("wos_api_key","")
    if not key: return results
    data, status = fetch_json(
        f"https://api.clarivate.com/apis/wos-starter/v1/documents"
        f"?db=WOS&q={urllib.parse.quote(query)}&limit={max_r}&page=1",
        headers={"X-ApiKey":key})
    if not data or status!=200: return results
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
        scihub = f"{CONFIG['scihub_mirrors'][0]}/{doi}" if doi else None
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
        except: pass
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
    return render_template("index.html",
                           ai_on=bool(CONFIG.get("anthropic_api_key")),
                           history=SESSION["history"][-10:],
                           saved=load_saved(),
                           show_onboarding=not CONFIG.get("onboarding_seen", False),
                           app_version=get_local_version())

@app.route("/onboarding/dismiss", methods=["POST"])
def onboarding_dismiss():
    CONFIG["onboarding_seen"] = True
    save_config(CONFIG)
    return jsonify({"ok": True})

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

@app.route("/search", methods=["POST"])
def search():
    data    = request.json
    query   = data.get("query","").strip()
    sources = data.get("sources",[])
    max_r   = int(data.get("max_results", MAX_RESULTS_DEFAULT))
    y_from  = int(data["year_from"]) if data.get("year_from") else None
    y_to    = int(data["year_to"])   if data.get("year_to")   else None

    if not query: return jsonify({"error":"Empty query"}), 400

    SESSION["articles"] = []
    SESSION["query"]    = query
    SESSION["last_synthesis"] = ""
    if query not in SESSION["history"]: SESSION["history"].append(query)

    seen    = make_dedup_set()
    results = []
    mesh    = []
    total_pubmed = 0

    SOURCE_MAP = {
        "pubmed":         lambda: search_pubmed(query, max_r, y_from, y_to, seen),
        "cochrane":       lambda: search_cochrane(query, max_r, y_from, y_to, seen),
        "arxiv":          lambda: search_arxiv(query, max_r, y_from, y_to, seen),
        "clinicaltrials": lambda: search_clinicaltrials(query, max_r, y_from, y_to, seen),
        "medrxiv":        lambda: search_biorxiv(query,"medrxiv",max_r,y_from,y_to,seen),
        "biorxiv":        lambda: search_biorxiv(query,"biorxiv",max_r,y_from,y_to,seen),
        "scopus":         lambda: search_scopus(query, max_r, y_from, y_to, seen),
        "wos":            lambda: search_wos(query, max_r, y_from, y_to, seen),
    }

    if "pubmed" in sources or "all" in sources:
        mesh = get_mesh(query)
        res, total_pubmed = search_pubmed(query, max_r, y_from, y_to, seen)
        results.extend(res)

    for src, fn in SOURCE_MAP.items():
        if src == "pubmed": continue
        if src in sources or "all" in sources:
            r = fn()
            if isinstance(r, tuple): r = r[0]
            results.extend(r)

    SESSION["articles"] = results
    return jsonify({
        "articles": results,
        "mesh": mesh,
        "total_pubmed": total_pubmed,
        "count": len(results),
    })

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

    if not query:
        return Response("data: "+json.dumps({"type":"error","text":"Empty query"})+"\n\n",
                        mimetype="text/event-stream")

    # Reset session up front
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
                            lambda: search_cochrane(query, max_r, y_from, y_to, seen, strict=strict)))
        if "pubmed" in sources or "all" in sources:
            runners.append(("pubmed", "PubMed",
                            lambda: search_pubmed(query, max_r, y_from, y_to, seen, strict=strict)))
        if "scopus" in sources or "all" in sources:
            runners.append(("scopus", "Scopus",
                            lambda: (search_scopus(query, max_r, y_from, y_to, seen), 0)))
        if "wos" in sources or "all" in sources:
            runners.append(("wos", "Web of Science",
                            lambda: (search_wos(query, max_r, y_from, y_to, seen), 0)))
        if "clinicaltrials" in sources or "all" in sources:
            runners.append(("clinicaltrials", "ClinicalTrials.gov",
                            lambda: (search_clinicaltrials(query, max_r, y_from, y_to, seen), 0)))
        if "arxiv" in sources or "all" in sources:
            runners.append(("arxiv", "arXiv",
                            lambda: (search_arxiv(query, max_r, y_from, y_to, seen), 0)))
        if "medrxiv" in sources or "all" in sources:
            runners.append(("medrxiv", "medRxiv",
                            lambda: (search_biorxiv(query,"medrxiv",max_r,y_from,y_to,seen), 0)))
        if "biorxiv" in sources or "all" in sources:
            runners.append(("biorxiv", "bioRxiv",
                            lambda: (search_biorxiv(query,"biorxiv",max_r,y_from,y_to,seen), 0)))
        return runners

    def generate():
        seen = make_dedup_set()
        all_results = []
        ai_on = bool((CONFIG.get("anthropic_api_key","") or "").strip())

        # Send an initial padding comment to defeat buffering in some webviews.
        yield ":" + (" " * 2048) + "\n\n"

        # 1. MeSH first (fast, gives the user something immediately)
        if "pubmed" in sources or "all" in sources:
            mesh = get_mesh(query)
            yield "data: " + json.dumps({"type":"mesh","mesh":mesh}) + "\n\n"

        runners = make_runners(seen)
        total_sources = len(runners)
        global_idx = 0   # running index assigned to each article

        # Each source: announce start → emit each article card immediately →
        # then stream one-liners as they finish (cards fill in live).
        for i, (key, label, fn) in enumerate(runners, 1):
            yield "data: " + json.dumps({
                "type":"source_start", "source":label,
                "index":i, "total":total_sources
            }) + "\n\n"
            yield ":keep-alive\n\n"
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
                except: pass
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

@app.route("/settings", methods=["GET","POST"])
def settings():
    global CONFIG
    if request.method == "POST":
        data = request.json
        for k in ("anthropic_api_key","pubmed_api_key","scopus_api_key",
                  "wos_api_key","unpaywall_email"):
            if k in data and data[k]: CONFIG[k] = data[k].strip()
        save_config(CONFIG)
        return jsonify({"ok": True})
    # Mask API keys (show only last 4 chars); the email isn't sensitive so
    # return it in full so the user can see and verify it.
    safe = {}
    for k, v in CONFIG.items():
        if not isinstance(v, str):
            continue
        if k == "unpaywall_email":
            safe[k] = v
        elif len(v) > 4:
            safe[k] = "*"*(len(v)-4) + v[-4:]
        else:
            safe[k] = "set" if v else ""
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
        except: return []
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
    import threading, socket

    # Find a free port (in case 5050 is taken)
    def free_port(preferred=5050):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", preferred)); s.close(); return preferred
        except OSError:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s2.bind(("127.0.0.1", 0)); port = s2.getsockname()[1]; s2.close(); return port

    PORT = free_port(5050)
    URL  = f"http://127.0.0.1:{PORT}"

    def run_server():
        app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True, use_reloader=False)

    # Try to open a native window via pywebview; fall back to a browser tab.
    try:
        import webview  # pywebview
        # Start Flask in a background thread
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        print(f"\n  🔬  MedSearch {LOCAL_VERSION}  —  native window on {URL}\n")
        webview.create_window(
            "MedSearch",
            URL,
            width=1280, height=860,
            min_size=(940, 640),
        )
        webview.start()   # blocks until window closed; then process exits cleanly
    except ImportError:
        import webbrowser
        print(f"\n  🔬  MedSearch {LOCAL_VERSION}  —  starting…")
        print(f"  (pywebview not installed — opening in browser instead)")
        print(f"  Open: {URL}\n")
        threading.Timer(1.2, lambda: webbrowser.open(URL)).start()
        run_server()
