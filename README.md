# MedSearch

**A desktop app for searching the medical and scientific literature — many databases at once, with AI summaries, an in-app PDF reader, and a research assistant.**

Built by [Riccardo Nevoso](https://github.com/H4lBarAd11) for clinicians and researchers who want one fast, friendly place to search the literature without juggling a dozen browser tabs.

MedSearch runs as a **native desktop window** (macOS, Windows, Linux). It searches PubMed, Cochrane, ClinicalTrials.gov, arXiv, Scopus, and Web of Science simultaneously, removes duplicates, finds free full-text where it exists, and — if you add a Claude API key — summarizes and helps you reason about the results.

---

## What it does

- **Searches six databases at once** — PubMed/MEDLINE, Cochrane Reviews, ClinicalTrials.gov, arXiv, Scopus, and Web of Science — and merges the results into one deduplicated list.
- **Clinical practice guidelines** — a "Guidelines" source surfaces national and society guidelines indexed in PubMed (works for many countries). Plus a **National guidelines** button that opens your country's official body directly — SNLG (Italy), NICE (UK), ECRI (US), AWMF (Germany), HAS (France) — with your search term pre-filled where the site allows.
- **Sort by relevance or recency** — a toggle beside the search bar reorders results by best match or newest-first, applied per database.
- **Finds free full-text** via Unpaywall and OpenAlex, with a Sci-Hub fallback when no open-access copy exists.
- **Reads PDFs in-app** — a built-in viewer with fit-to-width, zoom, and save-to-disk. Open-access *and* Sci-Hub PDFs open right inside the window.
- **Institutional library access** — point MedSearch at your university's EZProxy/OpenAthens and paywalled papers your institution subscribes to open through your library login, in a built-in browser window. Save several institutions and switch between them from the search bar.
- **AI summaries** (optional, needs a Claude API key) — a one-line takeaway per article, a streamed multi-paragraph synthesis across all results, and a per-paper "Explain" breakdown.
- **AI research assistant** — a chat panel grounded in your current search results. Ask things like *"which of these support X?"* and it answers citing the papers on your screen, or answers general clinical questions.
- **Citation graphs** — see what a paper cites and what cites it.
- **Export** to Markdown, BibTeX, or RIS, or send straight to **Zotero** (if the desktop app is running).
- **Quality-of-life**: journal quartile badges, MeSH term hints, year filters, saved searches, recent-search history, and a built-in settings panel for API keys (no config files to edit).
- **Friendly onboarding** — a first-launch guide (English / Italiano) and one-click auto-update.
- **macOS menu-bar quick search** (optional companion app) — a 🔍 MedSearch icon in your status bar. Click it, type a query, and the full MedSearch window opens with results already loading, searching whichever database you've set as your default. Recent searches and the default-source picker live right in the dropdown. The menu uses native macOS styling (Liquid Glass on Tahoe), so it blends in with the system.

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

### macOS — menu-bar quick search (optional)

A lightweight status-bar companion that lets you start a search from anywhere without first opening the main window.

```bash
pip3 install rumps          # one-time; macOS only
python3 menubar.py          # run it directly…
# …or build a background app you can add to Login Items:
bash make_menubar_app.sh
```

`make_menubar_app.sh` builds **"MedSearch Menu Bar.app"** — a background app with no Dock icon (just the menu-bar glyph). Add it to **System Settings ▸ General ▸ Login Items** to have it start automatically. Keep it in the same folder as `app.py`, since it launches the main app from beside itself. The menu bar and its dropdown use native macOS components, so they automatically adopt the system look (Liquid Glass on macOS Tahoe 26+) — your sage-green theme stays where it belongs, in the main app window.

**Known limitation — where results open:** when you run a quick search, what happens depends on whether MedSearch is already running:
- **Not running yet** → the menu-bar app launches the main app and its **native window opens straight on your results**. This is the usual case.
- **Already running** → a separate process can't re-point an existing native window, so the search opens in your **default browser** instead (same app, full functionality, just a browser tab rather than the native window).

This is an inherent constraint of the native-window approach, not a bug. For the common "quick capture when MedSearch isn't already open" case, you get the native window.

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

## Reading paywalled papers (institutional access)

Many papers aren't open-access but *are* available through a university subscription. MedSearch can route those through your library so they open with your institutional access.

In **⚙ Settings → Institutional libraries**, add your university's proxy address (EZProxy or OpenAthens). To find it: open any journal article *through your library's website* while off-campus, and copy the part of the address that appears in front of the publisher's name (e.g. `ezp.biblio.unitn.it`). Both the hostname-rewriting style and a `…?url=` login prefix are supported.

Once set, paywalled papers show a **"DOI (via library)"** button that opens them in a built-in browser window carrying your login — so subscribed papers load directly. You can save several institutions and switch the active one from the picker beside the search bar. Anything your library doesn't cover still falls back to a DOI link and a Sci-Hub option.

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
├── menubar.py            ← macOS menu-bar quick-search companion (optional)
├── make_menubar_app.sh   ← Builds "MedSearch Menu Bar.app" (macOS)
├── requirements.txt      ← Python dependencies (rumps is optional, macOS only)
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
Some publisher links are landing pages rather than direct PDFs, and Sci-Hub occasionally lacks a paper. In those cases MedSearch opens the article in the built-in browser automatically (and closes the empty PDF window), so you can read it through your institutional login if you have one set up.

**The menu-bar app icon doesn't appear.**
Make sure `rumps` is installed (`pip3 install rumps`) and that you launched `menubar.py` (or "MedSearch Menu Bar.app"). If you built the app, it's a background app — there's no Dock icon by design; look for 🔍 MedSearch in the menu bar at the top of the screen. If a quick search does nothing, confirm `menubar.py` is in the same folder as `app.py`.

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
