# MedSearch

**A desktop app for searching the medical and scientific literature — many databases at once, with AI summaries, an in-app PDF reader, and a research assistant.**

Built by [Riccardo Nevoso](https://github.com/H4lBarAd11) for clinicians and researchers who want one fast, friendly place to search the literature without juggling a dozen browser tabs.

MedSearch runs as a **native desktop window** (macOS, Windows, Linux). It searches PubMed, Cochrane, ClinicalTrials.gov, arXiv, Scopus, and Web of Science simultaneously, removes duplicates, finds free full-text where it exists, and — if you add a Claude API key — summarizes and helps you reason about the results.

---

## What it does

- **Searches six databases at once** — PubMed/MEDLINE, Cochrane Reviews, ClinicalTrials.gov, arXiv, Scopus, and Web of Science — and merges the results into one deduplicated list.
- **Finds free full-text** via Unpaywall, with a Sci-Hub fallback when no open-access copy exists.
- **Reads PDFs in-app** — a built-in viewer with fit-to-width, zoom, and save-to-disk. Open-access *and* Sci-Hub PDFs open right inside the window; no browser needed.
- **AI summaries** (optional, needs a Claude API key) — a one-line takeaway per article, a streamed multi-paragraph synthesis across all results, and a per-paper "Explain" breakdown.
- **AI research assistant** — a chat panel grounded in your current search results. Ask things like *"which of these support X?"* and it answers citing the papers on your screen, or answers general clinical questions.
- **Citation graphs** — see what a paper cites and what cites it.
- **Export** to Markdown, BibTeX, or RIS, or send straight to **Zotero** (if the desktop app is running).
- **Quality-of-life**: journal quartile badges, MeSH term hints, year filters, saved searches, recent-search history, and a built-in settings panel for API keys (no config files to edit).
- **Friendly onboarding** — a first-launch guide (English / Italiano) and one-click auto-update.

---

## Install

You need **Python 3.8+** and **Git**. (Both are pre-installed on most Macs; Windows users install Python from [python.org](https://python.org) and tick *"Add Python to PATH"*.)

```bash
git clone https://github.com/H4lBarAd11/MedSearch-by-RN.git
cd MedSearch-by-RN
pip install -r requirements.txt
python app.py        # use python3 on macOS/Linux
```

MedSearch opens in its own desktop window. (If the native-window library isn't available, it falls back to opening in your browser automatically.)

### macOS — double-clickable app

To get a real app you can keep in your Dock (no Terminal needed to launch):

```bash
bash make_app.sh
```

This builds **MedSearch.app** in the project folder. Drag it to `/Applications` or your Dock and launch it like any other app.

### macOS — one-line terminal shortcut

To launch from any terminal with a single command:

```bash
echo 'alias medsearchgui="cd /path/to/MedSearch-by-RN && python3 app.py"' >> ~/.zshrc
source ~/.zshrc
medsearchgui
```

### Windows

```bat
git clone https://github.com/H4lBarAd11/MedSearch-by-RN.git
cd MedSearch-by-RN
pip install -r requirements.txt
python app.py
```

MedSearch opens in its own window. To build a standalone `.exe` that colleagues can run without installing Python, run `build.bat` and share the resulting `dist\` folder.

---

## API keys

MedSearch works out of the box with the free databases — **no keys required** for PubMed, Cochrane, ClinicalTrials.gov, or arXiv. Keys unlock optional features:

| Key | Where to get it | Unlocks |
|---|---|---|
| **Anthropic (Claude)** | [console.anthropic.com](https://console.anthropic.com) | All AI features: summaries, synthesis, Explain, and the research assistant |
| **NCBI / PubMed** | [ncbi.nlm.nih.gov/account](https://ncbi.nlm.nih.gov/account) | Higher PubMed rate limits (10 vs 3 req/sec) |
| **Scopus** | [dev.elsevier.com](https://dev.elsevier.com) | Scopus as a search source |
| **Web of Science** | [developer.clarivate.com](https://developer.clarivate.com) | Web of Science as a search source |
| **Unpaywall** | any valid email | Open-access PDF detection |

Add keys from inside the app: **⚙ Settings & API keys**. They're saved locally to `~/.medsearch/config.json` and never leave your machine.

> **⚠️ Scopus & Web of Science need your institution's network.** These APIs authenticate by IP address, not just the key. From home you'll get a **401 error** — connect to your university VPN, or ask your library for an Elsevier *institutional token* (there's a field for it in Settings). On campus, the key alone works.

---

## How AI cost is kept low

If you use the AI features, MedSearch is careful with tokens: one-line summaries use the cheap Haiku model, the assistant reuses a cached, trimmed context across turns, and outputs are capped. A typical multi-turn assistant conversation costs roughly a few cents. You can turn AI off entirely with the **AI on/off toggle** in the top bar if you'd rather just search.

---

## Project structure

```
MedSearch-by-RN/
├── app.py                ← Flask backend + native-window launcher
├── templates/
│   └── index.html        ← The entire UI (HTML/CSS/JS)
├── requirements.txt      ← Python dependencies
├── make_app.sh           ← Builds MedSearch.app (macOS)
├── build.sh / build.bat  ← Standalone builds (macOS·Linux / Windows)
├── launch_gui.sh         ← Simple dev launcher (macOS / Linux)
├── icon.svg              ← App icon
└── VERSION               ← Current version
```

---

## Troubleshooting

**Scopus / Web of Science return nothing (401).**
You're off your institution's network. Use your university VPN, or add an Elsevier institutional token in Settings. See the note above.

**A PDF won't open in the viewer.**
Some publisher links are landing pages rather than direct PDFs, and Sci-Hub occasionally lacks a paper. MedSearch shows a clear message and an "↗ Open in browser" fallback in those cases.

**PubMed rate-limiting (HTTP 429).**
Add a free NCBI API key in Settings — it raises the limit from 3 to 10 requests/sec.

**An API key isn't being picked up.**
Re-enter it in ⚙ Settings and Save. Most keys take effect immediately; restart the app if one still isn't working.

**"externally managed environment" error from pip (macOS/Linux).**
Use a virtual environment:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## Privacy

Everything runs locally on your machine. Your searches, API keys, and saved data stay on your computer. The only outbound traffic is to the literature databases you search and — if you enable AI — the Anthropic API.

---

## License

MIT — free to use, modify, and distribute.
