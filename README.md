# OnionExplorer — Dark Web Threat Intelligence Dashboard
Project Made By Piyush G 
OnionExplorer is an enterprise-grade dark web threat intelligence platform that dynamically aggregates, parses, and monitors ransomware groups, leaked forums, marketplaces, and Telegram darknet invite links. 
Piyush G
---

## 🚀 Key Features

* **Multi-Feed Cybersecurity Ingestion**: Concurrently crawls and processes multiple OSINT CTI feeds including RansomFeed.it, RansomLook.io, Ransomware.live, and custom threat intelligence feeds from GitHub.
* **Lightweight Light & Dark Themes**: Sleek, modern cybersecurity dash layout equipped with HSL tailored cards, micro-animations, and a one-click Light/Dark UI theme toggle with localStorage persistence.
* **Responsive Visual Counters**: Six interactive top counters (Forums, Markets, Telegram, URLs, Online, Offline) that dynamically recalculate metrics based on selected source filters.
* **Auto-Tab Switching**: Intuitively redirects active dashboard tab selections (e.g. shifts to 💬 Telegram Links or 🛍️ Markets) when selecting a source filter to prevent empty dashboard states.
* **Enterprise Exporters**:
  - **CSV Export**: Sanitized against CSV injection attacks (`=`, `+`, `-`, `@` escapes) and embedded with generation metadata headers for corporate audits.
  - **Markdown Export**: Generates table-formatted threat reports containing executive summaries, matching entity counts, and corporate intelligence disclaimers.
* **Production-Grade Infrastructure**: Powered by APScheduler background task management, Rotating File Logging (keeps up to 5 historical log backups), and multi-threaded session connection pool mounts to prevent network throttling warnings.

---

## 📂 Project Structure

```
OnionExplorer/
├── data/                    # Persistent storage database & logs
│   ├── config.json          # Crawling source list config
│   ├── onion_explorer.db    # Relational SQLite database
│   └── logs/                # Rotating server logs
├── monitors/                # Custom scraper modules
│   ├── github_feed.py       # GitHub .md markdown feeds scraper
│   ├── ransomelive.py       # Ransomware.live scraper
│   ├── ransomelook.py       # RansomLook API scraper
│   ├── ransomfeed.py        # RansomFeed web scraper
│   └── telegram_checker.py  # Telegram invite links validator
├── onion_explorer/          # Local client database abstraction layer
├── static/                  # JavaScript & stylesheet assets
├── templates/               # Flask html templates
├── tests/                   # Unified test suite files
├── wsgi.py                  # Gunicorn gate entrypoint
├── requirements.txt         # Package dependencies
└── main.py                  # Application entry point
```

---

## 🛠️ How to Deploy & Run on Ubuntu Server

### ✅ One-Time Setup (Only required on first deployment)

#### Step 1 — Install System Dependencies
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git
```

#### Step 2 — Clone the Repository
```bash
git clone https://github.com/Piyush2425/OnionExplorer.git
cd OnionExplorer
```

#### Step 3 — Create Virtual Environment & Install Packages
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 4 — Configure Environment
```bash
nano .env
```
Paste the following settings and save (`CTRL+O → Enter → CTRL+X`):
```ini
PORT=5000
HOST=0.0.0.0
DB_TYPE=sqlite
SCRAPE_INTERVAL_MINUTES=1440
LOG_LEVEL=INFO
SECRET_KEY=generate-a-secure-random-key-here
```

---

### 🚀 Start the Server (Every Time)

After completing the one-time setup above, simply run **this single command** from the project root folder to start both the backend API and the frontend dashboard automatically:

```bash
bash serve.sh
```

Alternatively, you can start it directly via the Python CLI tool:
```bash
source venv/bin/activate
pip install -e .
onion serve
```

> **Dashboard is live at**: `http://localhost:5173` (Frontend Console with Proxy to Flask API on `http://127.0.0.1:5000`)

---

### 🛑 Stop the Server
Simply press `CTRL+C` in your terminal to shut down both the Flask API server and SvelteKit dev server gracefully.


---

## 🔒 Automated Tor Screenshot Verification & Manual UI Scanning

OnionExplorer is integrated with a secure headless Firefox browser verifier that crawls darkweb Onion sites, checks link statuses, and captures full-page screenshot previews to render directly on your dashboard.

### ⚙️ How It Works (Tor Proxy)
* The system checks if a local Tor SOCKS5 service is listening on port `9050`.
* If detected, all Firefox browser crawls are routed via SOCKS5 SOCKS proxy settings with remote DNS resolution enabled (`network.proxy.socks_remote_dns = true`) to load onion URLs securely.
* If Tor is not running, it falls back to direct routing (ideal for local testing of standard web feeds).
* **Security Filter**: Telegram links are completely skipped and labeled as `N/A (Telegram)` since they cannot be loaded via standard browser verification.
* **Manual Verification Only**: The application **does not** automatically take screenshots of all links at startup. Screenshots are only taken when you explicitly click the check button on the UI.

### 🔧 Installing Firefox & Tor on Ubuntu Server

To get automated screenshots running on your Ubuntu virtual machine, install Tor, Firefox, and dependencies:

```bash
# 1. Install Tor Service
sudo apt update
sudo apt install -y tor
sudo systemctl enable tor
sudo systemctl start tor

# 2. Verify Tor is listening on port 9050
ss -nltp | grep 9050

# 3. Install Firefox (standard Ubuntu package)
sudo apt install -y firefox

# 4. Verify geckodriver auto-installer can run
# webdriver_manager handles downloading geckodriver binary automatically inside the virtualenv
```

### 📟 How to Verify and Scan Links via UI

1. Open the dashboard table and click the **arrow `▶`** next to any threat actor to expand its Onion locations.
2. The row shows a **`No Preview`** placeholder by default.
3. Click the **`🔄 Re-Check`** action button inside the target link row.
4. The button changes to **`🔄 Queued...`** and then **`⏳ Processing...`** as the background thread launches Firefox, connects via Tor, and waits exactly **30 seconds** for full loading before capturing the screenshot.
5. Once completed, the status dot updates automatically (e.g. `Up` or `Down`) and a **thumbnail image preview** replaces the placeholder.
6. **Click the thumbnail preview** to open a premium lightbox zoom window and view the screenshot in full resolution.

---

### 📄 View Logs
```bash
tail -f data/logs/gunicorn.log
```


