# Paper to Phone

Research papers reflowed for your phone, with every figure, table and reference.

**Open the app:** https://gabrielpacosta.github.io/paper-to-phone/ (on iPhone, use Share, then Add to Home Screen).
**See a sample first:** https://gabrielpacosta.github.io/paper-to-phone/?demo

## Convert on the phone

Tap **Start reading**, then **+**, and pick a PDF from Files, paste a copied PDF, or paste an arXiv link.
The app converts the PDF on the device: `converter/paper2phone.py` runs inside the browser through
[Pyodide](https://pyodide.org) (Python compiled to WebAssembly) with PyMuPDF's WebAssembly build.
The first conversion downloads the converter (about 20 MB, then cached); later ones take a few seconds.
The PDF never leaves the device.

## Optional: sync through OneDrive

```
iPhone, Mac, PC ──add PDFs, links or reader files──▶  OneDrive / Paper to Phone / Inbox
                                                                 │
                                   a Mac or PC running paper2phone.py converts it
                                                                 ▼
iPhone, Mac, PC ◀──open, read, resume anywhere──────  OneDrive / Paper to Phone / Library
```

| Folder | What lives there |
|---|---|
| `Inbox/` | PDFs and link files waiting to be converted |
| `Library/` | Reader files (`.html`) and EPUBs for Apple Books |
| `Originals/` | PDFs that were converted |
| `Could not convert/` | Items that failed, each with a note explaining why |
| `.paper2phone/state.json` | Reading positions, recently opened papers and reader settings, shared by all devices |

The app has no server. It signs in with your Microsoft account and talks to OneDrive directly from your browser.
Papers never pass through anything else.

## Connect it to Microsoft (once, about 5 minutes)

The app needs its own Microsoft app ID before anyone can sign in.

1. Open https://entra.microsoft.com and sign in. Go to **App registrations**, then **New registration**.
   (If it asks you to create a directory or tenant first, follow the free option.)
2. **Name:** Paper to Phone.
3. **Supported account types:** *Accounts in any organizational directory and personal Microsoft accounts*.
4. **Redirect URI:** platform *Single-page application (SPA)*, address `https://gabrielpacosta.github.io/paper-to-phone/`.
5. Click **Register**, then copy the **Application (client) ID**.
6. Under **API permissions**, add Microsoft Graph delegated permission **Files.ReadWrite** (User.Read is already there).
7. Put the ID in `config.js` (`clientId: "…"`), or paste it into the setup box on the app's first screen.

Work or school accounts work when the organization lets users approve apps; otherwise its IT team must approve
Paper to Phone once.

## Convert papers on a computer

1. Install Python 3 and PyMuPDF: `pip3 install pymupdf`
2. Download [`converter/paper2phone.py`](converter/paper2phone.py).
3. Point it at your synced folder once; it then starts at every login:

```
# Mac
python3 paper2phone.py --install-service "$HOME/Library/CloudStorage/OneDrive-Personal/Paper to Phone"
# Windows
py paper2phone.py --install-service "%USERPROFILE%\OneDrive\Paper to Phone"
```

It also converts single files, links and DOIs directly:

```
python3 paper2phone.py paper.pdf
python3 paper2phone.py https://arxiv.org/abs/1706.03762
python3 paper2phone.py 10.1371/journal.pone.0348975
python3 paper2phone.py --clipboard          # a PDF, link or DOI you copied
```

## Privacy

- Sign-in tokens stay in your browser. The app asks only for access to your files and your name.
- The DOI of a paper is sent to Crossref to look up the journal name; use `--offline` to skip that.
- Papers you open are cached in your browser for offline reading. Signing out clears that cache.
