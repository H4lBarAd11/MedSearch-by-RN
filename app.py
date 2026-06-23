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
    key = CONFIG.get("anthropic_api_key","")
    if not key: return None
    payload = json.dumps({"model":"claude-sonnet-4-6","max_tokens":max_tokens,
                          "stream":stream,"messages":messages}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key":key,"anthropic-version":"2023-06-01",
                 "content-type":"application/json"}, method="POST")
    try: return urllib.request.urlopen(req, timeout=60)
    except: return None

def ai_oneliner(title, abstract):
    if not CONFIG.get("anthropic_api_key") or not abstract: return None
    prompt = (f"Title: {title}\n\nAbstract: {abstract}\n\n"
              "In exactly one sentence (≤25 words), state the key finding. No preamble.")
    r = _claude([{"role":"user","content":prompt}], max_tokens=80)
    if r:
        try: return json.loads(r.read().decode())["content"][0]["text"].strip()
        except: pass
    return None

def ai_synthesis_stream(query, articles):
    """Generator that yields SSE chunks for the synthesis."""
    key = CONFIG.get("anthropic_api_key","")
    if not key: yield "data: " + json.dumps({"type":"error","text":"No API key."}) + "\n\n"; return
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
    except Exception as e:
        yield "data: " + json.dumps({"type":"error","text":str(e)}) + "\n\n"
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

def search_pubmed(query, max_r, y_from, y_to, seen):
    results = []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    kp   = f"&api_key={CONFIG['pubmed_api_key']}" if CONFIG.get("pubmed_api_key") else ""
    dp   = (f"&mindate={y_from or 1900}/01/01&maxdate={y_to or 2099}/12/31&datetype=pdat"
            if y_from or y_to else "")
    data, _ = fetch_json(f"{base}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(query)}"
                         f"&retmax={max_r}&retmode=json{kp}{dp}")
    if not data: return results, 0
    ids   = data.get("esearchresult",{}).get("idlist",[])
    total = int(data.get("esearchresult",{}).get("count",0))
    if not ids: return results, total
    body, _ = http_get(f"{base}/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml{kp}")
    if not body: return results, total
    root = ET.fromstring(body)
    for art in root.findall(".//PubmedArticle"):
        med    = art.find(".//MedlineCitation")
        art_el = med.find("Article") if med is not None else None
        if art_el is None: continue
        title   = "".join((art_el.find("ArticleTitle") or ET.Element("x")).itertext()) or "No title"
        journal = (art_el.find(".//Journal/Title") or ET.Element("x")).text or ""
        year    = (art_el.find(".//Journal/JournalIssue/PubDate/Year") or ET.Element("x")).text or "n.d."
        if not within_range(year, y_from, y_to): continue
        aus = []
        for au in art_el.findall(".//AuthorList/Author")[:3]:
            ln = au.find("LastName"); fn = au.find("ForeName")
            if ln is not None: aus.append(f"{ln.text}{', '+fn.text[0]+'.' if fn is not None else ''}")
        authors = "; ".join(aus) + (" et al." if len(art_el.findall(".//AuthorList/Author"))>3 else "")
        doi  = next((a.text for a in art.findall(".//ArticleId") if a.get("IdType")=="doi"), None)
        pmid = (art.find(".//MedlineCitation/PMID") or ET.Element("x")).text
        abs_el   = art_el.find(".//Abstract/AbstractText")
        abstract = "".join(abs_el.itertext()) if abs_el is not None else ""
        if is_duplicate(seen, doi, title): continue
        register(seen, doi, title)
        kind, link = resolve_access(doi)
        scihub = f"{CONFIG['scihub_mirrors'][0]}/{doi}" if doi else None
        results.append({"title":title,"authors":authors,"year":year,"journal":journal,
                        "quartile":get_quartile(journal),"doi":doi,"pmid":pmid,
                        "abstract":abstract,"source":"PubMed","access_kind":kind,
                        "access_link":link,"scihub":scihub,
                        "oneliner":ai_oneliner(title, abstract)})
        time.sleep(0.2)
    return results, total

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
                        "scihub":None,"oneliner":ai_oneliner(title,summary)})
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
                        "scihub":None,"oneliner":ai_oneliner(title,brief)})
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
                            "oneliner":ai_oneliner(title,abstract)})
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
                        "oneliner":ai_oneliner(title,abstract)})
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
                        "oneliner":ai_oneliner(title,abstract)})
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
                           history=SESSION["history"][-10:])

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

    # Cochrane — browser link only
    cochrane_link = None
    if "cochrane" in sources or "all" in sources:
        encoded = urllib.parse.quote(query.replace(" ","+"))
        cochrane_link = f"https://www.cochranelibrary.com/search?searchBy=6&searchText={encoded}"

    SESSION["articles"] = results
    return jsonify({
        "articles": results,
        "mesh": mesh,
        "total_pubmed": total_pubmed,
        "cochrane_link": cochrane_link,
        "count": len(results),
    })

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

@app.route("/settings", methods=["GET","POST"])
def settings():
    global CONFIG
    if request.method == "POST":
        data = request.json
        for k in ("anthropic_api_key","pubmed_api_key","scopus_api_key",
                  "wos_api_key","unpaywall_email"):
            if k in data and data[k]: CONFIG[k] = data[k]
        save_config(CONFIG)
        return jsonify({"ok": True})
    safe = {k: ("*"*(len(v)-4)+v[-4:] if len(v)>4 else ("set" if v else ""))
            for k,v in CONFIG.items() if isinstance(v,str)}
    return jsonify(safe)

@app.route("/history")
def history():
    return jsonify(SESSION["history"][-20:])

if __name__ == "__main__":
    import webbrowser, threading
    print("\n  🔬  MedSearch v4.0  —  starting…")
    print("  Open: http://localhost:5050\n")
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5050")).start()
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
