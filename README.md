# MedSearch

**Comprehensive medical & scientific literature search tool with AI summaries.**  
Built by [Riccardo Nevoso](https://github.com/H4lBarAd).

Searches PubMed, Scopus, Web of Science, Cochrane, ClinicalTrials.gov, arXiv, medRxiv and bioRxiv — all at once, deduplicated, with open-access detection, journal quartile badges, and AI-powered per-article summaries and end-of-session synthesis via Claude.

Available as a **web GUI** (recommended) or a **terminal tool**.

---

## Quick install (macOS / Linux)

You need Python 3.8+ and Git. Both come pre-installed on modern Macs.

```bash
git clone https://github.com/H4lBarAd/MedSearch.git
cd MedSearch
bash launch_gui.sh
```

The launcher installs all dependencies automatically on first run, then opens **http://localhost:5050** in your browser.

> **Windows colleagues:** see the [Windows instructions](#windows) below.

---

## Quick install (macOS — one-liner shortcut)

To launch MedSearch from any terminal window with a single command:

```bash
echo 'alias medsearchgui="bash /path/to/MedSearch/launch_gui.sh"' >> ~/.zshrc
source ~/.zshrc
medsearchgui
```

---

## Features

| Feature | Details |
|---|---|
| **8 databases** | PubMed · Scopus · Web of Science · Cochrane · ClinicalTrials.gov · arXiv · medRxiv · bioRxiv |
| **AI one-liner** | Single-sentence plain-language summary per article (Claude) |
| **AI synthesis** | Streamed 3–5 paragraph academic synthesis across all results |
| **Explain paper** | Deep per-article breakdown: question · methods · findings · limitations · implications |
| **Open access** | Unpaywall integration — free PDF link when available |
| **Sci-Hub** | One-click fallback when no free version exists |
| **Journal quartile** | Q1/Q2 badge next to journal names |
| **Deduplication** | Same paper across multiple databases shown only once |
| **MeSH suggestions** | PubMed query translation and MeSH term hints |
| **Date filter** | Restrict results to any year range |
| **Export** | Markdown report · BibTeX (.bib) · RIS (.ris) — saved to `~/medsearch_exports/` |
| **Search history** | Sidebar with recent queries, click to re-run |
| **Settings UI** | Enter API keys from inside the app — no config files to edit |

---

## API keys

All keys are optional except Anthropic (needed for AI features). Enter them in the **⚙ Settings** panel inside the app — no files to edit manually.

| Key | Where to get it | Cost |
|---|---|---|
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | ~$0.02–0.05 per session |
| **NCBI / PubMed** | [ncbi.nlm.nih.gov/account](https://ncbi.nlm.nih.gov/account) | Free |
| **Scopus** | [dev.elsevier.com](https://dev.elsevier.com) | Free |
| **Web of Science** | [developer.clarivate.com](https://developer.clarivate.com) | Free |
| **Unpaywall** | Any valid email address | Free |

Keys are saved locally to `~/.medsearch/config.json` and never leave your machine.

> **Note:** Cochrane Library has no public API. MedSearch opens a direct browser search link for Cochrane queries.

---

## Windows

Requirements: Python 3.8+ with "Add to PATH" ticked during install ([python.org](https://python.org)).

**Option A — run directly (simplest):**
```bat
git clone https://github.com/H4lBarAd/MedSearch.git
cd MedSearch
python -m pip install flask
python app.py
```
Then open http://localhost:5050 in your browser.

**Option B — build a standalone .exe (no Python needed on target machine):**
1. Double-click `build.bat`
2. Wait ~1 minute
3. Share the `dist\` folder with colleagues
4. They double-click `launch_medsearch.bat`

---

## Terminal mode

If you prefer the terminal over the browser GUI, `medsearch.py` works standalone:

```bash
python3 medsearch.py   # macOS / Linux
python medsearch.py    # Windows
```

A first-run wizard will ask for your API keys interactively.

---

## Project structure

```
MedSearch/
├── app.py                  ← Flask backend (GUI mode)
├── medsearch.py            ← Standalone terminal tool
├── launch_gui.sh           ← macOS/Linux launcher (use this)
├── build.sh                ← macOS/Linux PyInstaller build script
├── build.bat               ← Windows PyInstaller build script
├── launch_medsearch.bat    ← Windows terminal launcher
├── templates/
│   └── index.html          ← Web GUI frontend
└── README.md
```

---

## Troubleshooting

**Browser doesn't open automatically:**  
Go to [http://localhost:5050](http://localhost:5050) manually.

**"Address already in use" error:**  
Another instance is already running. Either use that one, or kill it:
```bash
lsof -ti:5050 | xargs kill
```

**PubMed rate limiting (HTTP 429):**  
Get a free NCBI API key — raises the limit from 3 to 10 requests/sec.

**API key not being picked up:**  
Open ⚙ Settings in the app and re-enter the key. Changes take effect immediately for most keys; restart the app if a key still isn't working.

**Sci-Hub links not opening:**  
Copy the printed URL manually into your browser.

**"externally managed environment" error on pip:**  
Use the virtual environment: `source .venv/bin/activate` before running pip.

---

## License

MIT — free to use, modify and distribute.
