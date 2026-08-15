#!/usr/bin/env python3
"""
OnionExplorer — Main Entry Point
Serves the dashboard and runs background scrapers on a configurable schedule.
"""

import os
import sys
import re

# Force UTF-8 encoding on Windows standard streams
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import json
import csv
import time
import threading
import logging
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.background import BackgroundScheduler
import importlib
import asyncio
import io
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, make_response, send_from_directory

# Import threat location library
from onion_explorer import ThreatLocationClient
from onion_explorer.exporters import export_to_json, export_to_csv, export_to_html
from onion_explorer.screenshot_worker import start_screenshot_worker, stop_screenshot_worker, queue_url_for_screenshot, get_screenshot_worker_status
from monitors import ransomfeed, ransomelook, ransomelive, github_feed, telegram_checker, watchguard

# ---------- Configuration ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MONITORS_DIR = os.path.join(BASE_DIR, "monitors")

# Scrape interval in minutes (configurable)
SCRAPE_INTERVAL_MINUTES = int(os.environ.get("SCRAPE_INTERVAL_MINUTES", "1440"))

# Data file paths
RANSOMFEED_URLS_JSON = os.path.join(DATA_DIR, "ransomfeed_all_source_urls.json")
RANSOMFEED_STATS_CSV = os.path.join(DATA_DIR, "ransomfeed_groups_stats.csv")
RANSOMLOOK_GROUPS_JSON = os.path.join(DATA_DIR, "ransomlook_groups.json")
RANSOMLOOK_MARKETS_JSON = os.path.join(DATA_DIR, "ransomlook_markets.json")
RANSOMLOOK_LINKS_CSV = os.path.join(DATA_DIR, "ransomlook_links.csv")
RANSOMWARE_LIVE_JSON = os.path.join(DATA_DIR, "ransomware_live_locations.json")
GITHUB_TELEGRAM_JSON = os.path.join(DATA_DIR, "github_telegram_links.json")
GITHUB_FORUMS_JSON = os.path.join(DATA_DIR, "github_forums_groups.json")
GITHUB_MARKETS_JSON = os.path.join(DATA_DIR, "github_markets.json")
WATCHGUARD_JSON = os.path.join(DATA_DIR, "watchguard_ransomware.json")

# ---------- Logging ----------
os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)
log_file = os.path.join(DATA_DIR, "logs", "onion_explorer.log")
log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("OnionExplorer")

# ---------- Flask App ----------
from flask_cors import CORS
app = Flask(__name__)
CORS(app)

# ---------- Scraper State ----------
scraper_state = {
    "last_scrape": None,
    "next_scrape": None,
    "is_running": False,
    "interval_minutes": SCRAPE_INTERVAL_MINUTES,
    "last_error": None,
    "scrape_count": 0
}
state_lock = threading.Lock()
scheduler = BackgroundScheduler()


# ═══════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════

def load_json_safe(filepath):
    """Load a JSON file, return None if missing or invalid."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def load_csv_as_dicts(filepath):
    """Load a CSV file as a list of dicts."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except IOError:
        return []


def get_file_mtime(filepath):
    """Return the last modified time as ISO string, or None."""
    if not os.path.exists(filepath):
        return None
    return datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════
#  DATA AGGREGATION
# ═══════════════════════════════════════════════

def make_entity(name):
    """Create a fresh entity dict."""
    return {
        "name": name,
        "type": "group",  # default, overridden for markets
        "sources": [],
        "urls": [],
        "stats": {"total": 0, "2025": 0, "2026": 0},
        "online_count": 0,
        "offline_count": 0,
        "total_urls": 0
    }


def build_unified_data_from_files():
    """
    Merge data from all sources into forums_groups, markets, and telegram_links.
    """
    forums_groups = {}
    markets = {}
    telegram_links = {}

    # ── 1. RansomFeed URLs (JSON & CSV fallback) ──
    rf_data = load_json_safe(RANSOMFEED_URLS_JSON)
    if rf_data and isinstance(rf_data, dict):
        for group_name, urls_list in rf_data.items():
            key = group_name.lower().strip()
            if key not in forums_groups:
                forums_groups[key] = make_entity(group_name)
            if "ransomfeed" not in forums_groups[key]["sources"]:
                forums_groups[key]["sources"].append("ransomfeed")
            for u in urls_list:
                forums_groups[key]["urls"].append({
                    "url": u.get("url", ""),
                    "status": u.get("status", "Unknown"),
                    "source": "ransomfeed",
                    "version": u.get("version", "")
                })
    else:
        # Fallback to CSV if JSON is missing/empty
        rf_csv = os.path.join(DATA_DIR, "ransomfeed_all_source_urls.csv")
        rf_rows = load_csv_as_dicts(rf_csv)
        for row in rf_rows:
            group_name = row.get("group", "").strip()
            url_val = row.get("url", "").strip()
            if not group_name or not url_val:
                continue
            key = group_name.lower().strip()
            if key not in forums_groups:
                forums_groups[key] = make_entity(group_name)
            if "ransomfeed" not in forums_groups[key]["sources"]:
                forums_groups[key]["sources"].append("ransomfeed")
            forums_groups[key]["urls"].append({
                "url": url_val,
                "status": row.get("status", "Unknown"),
                "source": "ransomfeed",
                "version": row.get("version", "")
            })

    # ── 2. RansomFeed Stats ──
    stats_rows = load_csv_as_dicts(RANSOMFEED_STATS_CSV)
    for row in stats_rows:
        gname = row.get("group", "").strip()
        if not gname:
            continue
        key = gname.lower()
        if key not in forums_groups:
            forums_groups[key] = make_entity(gname)
        try:
            forums_groups[key]["stats"]["total"] = int(row.get("total", 0))
            forums_groups[key]["stats"]["2025"] = int(row.get("2025", 0))
            forums_groups[key]["stats"]["2026"] = int(row.get("2026", 0))
        except (ValueError, TypeError):
            pass

    # ── 3. RansomLook Groups ──
    rl_groups = load_json_safe(RANSOMLOOK_GROUPS_JSON)
    if rl_groups and isinstance(rl_groups, dict):
        for gname, ginfo in rl_groups.get("groups", {}).items():
            key = gname.lower().strip()
            if key not in forums_groups:
                forums_groups[key] = make_entity(gname)
            if "ransomlook" not in forums_groups[key]["sources"]:
                forums_groups[key]["sources"].append("ransomlook")
            for loc in ginfo.get("locations", []):
                avail = loc.get("available", False)
                forums_groups[key]["urls"].append({
                    "url": loc.get("url", ""),
                    "status": "Online" if avail else "Offline",
                    "source": "ransomlook",
                    "fqdn": loc.get("fqdn", ""),
                    "version": str(loc.get("version", ""))
                })

    # ── 4. RansomLook Markets ──
    rl_markets = load_json_safe(RANSOMLOOK_MARKETS_JSON)
    if rl_markets and isinstance(rl_markets, dict):
        for mname, minfo in rl_markets.get("markets", {}).items():
            key = mname.lower().strip()
            if key not in markets:
                markets[key] = make_entity(mname)
                markets[key]["type"] = "market"
            if "ransomlook" not in markets[key]["sources"]:
                markets[key]["sources"].append("ransomlook")
            for loc in minfo.get("locations", []):
                avail = loc.get("available", False)
                markets[key]["urls"].append({
                    "url": loc.get("url", ""),
                    "status": "Online" if avail else "Offline",
                    "source": "ransomlook",
                    "fqdn": loc.get("fqdn", ""),
                    "version": str(loc.get("version", ""))
                })

    # ── 5. RansomwareLive Locations (all are groups) ──
    rlive_data = load_json_safe(RANSOMWARE_LIVE_JSON)
    if rlive_data and isinstance(rlive_data, dict):
        for slug, ginfo in rlive_data.items():
            key = slug.lower().strip()
            display = ginfo.get("name", slug)
            if key not in forums_groups:
                forums_groups[key] = make_entity(display)
            if "ransomware.live" not in forums_groups[key]["sources"]:
                forums_groups[key]["sources"].append("ransomware.live")
            for loc in ginfo.get("locations", []):
                avail = loc.get("available", False)
                forums_groups[key]["urls"].append({
                    "url": loc.get("url", ""),
                    "status": "Online" if avail else "Offline",
                    "source": "ransomware.live",
                    "fqdn": loc.get("fqdn", ""),
                    "last_visit": loc.get("last_visit", ""),
                    "server_info": loc.get("server_info", "")
                })

    # ── 6. GitHub Telegram Links ──
    gtg_data = load_json_safe(GITHUB_TELEGRAM_JSON)
    if gtg_data and isinstance(gtg_data, dict):
        for gkey, ginfo in gtg_data.items():
            key = gkey.lower().strip()
            display = ginfo.get("name", gkey)
            if key not in telegram_links:
                telegram_links[key] = make_entity(display)
                telegram_links[key]["type"] = "telegram"
            for loc in ginfo.get("locations", []):
                avail = loc.get("available", False)
                loc_src = loc.get("source", "github")
                if loc_src not in telegram_links[key]["sources"]:
                    telegram_links[key]["sources"].append(loc_src)
                loc_status = loc.get("status") or ("Online" if avail else "Not scanned yet")
                telegram_links[key]["urls"].append({
                    "url": loc.get("url", ""),
                    "status": loc_status,
                    "source": loc_src,
                    "url_type": "telegram"
                })

    # ── 7. GitHub Forums & Groups ──
    gfg_data = load_json_safe(GITHUB_FORUMS_JSON)
    if gfg_data and isinstance(gfg_data, dict):
        for gkey, ginfo in gfg_data.items():
            key = gkey.lower().strip()
            display = ginfo.get("name", gkey)
            if key not in forums_groups:
                forums_groups[key] = make_entity(display)
            for loc in ginfo.get("locations", []):
                avail = loc.get("available", False)
                loc_src = loc.get("source", "github")
                if loc_src not in forums_groups[key]["sources"]:
                    forums_groups[key]["sources"].append(loc_src)
                loc_status = loc.get("status") or ("Online" if avail else "Not scanned yet")
                forums_groups[key]["urls"].append({
                    "url": loc.get("url", ""),
                    "status": loc_status,
                    "source": loc_src,
                    "fqdn": loc.get("fqdn", ""),
                    "url_type": "leak_site"
                })

    # ── 8. GitHub Markets ──
    gm_data = load_json_safe(GITHUB_MARKETS_JSON)
    if gm_data and isinstance(gm_data, dict):
        for gkey, ginfo in gm_data.items():
            key = gkey.lower().strip()
            display = ginfo.get("name", gkey)
            if key not in markets:
                markets[key] = make_entity(display)
                markets[key]["type"] = "market"
            for loc in ginfo.get("locations", []):
                avail = loc.get("available", False)
                loc_src = loc.get("source", "github")
                if loc_src not in markets[key]["sources"]:
                    markets[key]["sources"].append(loc_src)
                loc_status = loc.get("status") or ("Online" if avail else "Not scanned yet")
                markets[key]["urls"].append({
                    "url": loc.get("url", ""),
                    "status": loc_status,
                    "source": loc_src,
                    "fqdn": loc.get("fqdn", ""),
                    "url_type": "market"
                })

    # ── 9. WatchGuard Scraped Data (JSON & CSV fallback) ──
    wg_items = load_json_safe(WATCHGUARD_JSON)
    if not wg_items:
        wg_csv = os.path.join(DATA_DIR, "watchguard_ransomware.csv")
        wg_items = load_csv_as_dicts(wg_csv)

    if wg_items and isinstance(wg_items, list):
        for item in wg_items:
            gname = item.get("group_name", "").strip()
            if not gname:
                continue
            key = gname.lower().strip()
            if key not in forums_groups:
                forums_groups[key] = make_entity(gname)
            if "watchguard" not in forums_groups[key]["sources"]:
                forums_groups[key]["sources"].append("watchguard")
            
            # Map stats
            forums_groups[key]["stats"]["watchguard_status"] = item.get("status", item.get("is_active", "Inactive"))
            if item.get("first_seen_display"):
                forums_groups[key]["stats"]["first_seen"] = item.get("first_seen_display")
            if item.get("last_seen_display"):
                forums_groups[key]["stats"]["last_seen"] = item.get("last_seen_display")

            onions_raw = item.get("onion_links", [])
            if isinstance(onions_raw, str):
                onions = [x.strip() for x in re.split(r'[,|;\s]+', onions_raw) if x.strip() and x.strip().lower() != "none"]
            elif isinstance(onions_raw, list):
                onions = [str(x).strip() for x in onions_raw if x and str(x).strip().lower() != "none"]
            else:
                onions = []

            for o in onions:
                u_str = o.strip()
                if not u_str.startswith("http"):
                    u_str = "http://" + u_str
                # Avoid duplicates
                if not any(existing_u.get("url") == u_str and existing_u.get("source") == "watchguard" for existing_u in forums_groups[key]["urls"]):
                    status_val = "Online" if str(item.get("status", "")).lower() == "active" or str(item.get("is_active", "")).lower() == "true" or str(item.get("is_active", "")).lower() == "active" else "Offline"
                    forums_groups[key]["urls"].append({
                        "url": u_str,
                        "status": status_val,
                        "source": "watchguard",
                        "last_visit": item.get("last_seen_display", "")
                    })

    # ── Compute counts ──
    for collection in (forums_groups, markets, telegram_links):
        for entity in collection.values():
            on = sum(1 for u in entity["urls"] if u["status"] == "Online")
            off = len(entity["urls"]) - on
            entity["online_count"] = on
            entity["offline_count"] = off
            entity["total_urls"] = len(entity["urls"])

    # ── Meta ──
    freshness = {}
    for fp, label in [
        (RANSOMFEED_URLS_JSON, "ransomfeed"),
        (RANSOMLOOK_GROUPS_JSON, "ransomlook"),
        (RANSOMWARE_LIVE_JSON, "ransomware.live"),
        (GITHUB_TELEGRAM_JSON, "github_telegram"),
        (GITHUB_FORUMS_JSON, "github_forums"),
        (GITHUB_MARKETS_JSON, "github_markets"),
        (WATCHGUARD_JSON, "watchguard"),
    ]:
        mt = get_file_mtime(fp)
        if mt:
            freshness[label] = mt

    with state_lock:
        meta = {
            "last_scraped": scraper_state["last_scrape"],
            "next_scrape": scraper_state["next_scrape"],
            "is_scraping": scraper_state["is_running"],
            "scrape_interval_minutes": scraper_state["interval_minutes"],
            "scrape_count": scraper_state["scrape_count"],
            "source_freshness": freshness
        }

    return {"forums_groups": forums_groups, "markets": markets, "telegram_links": telegram_links, "meta": meta}


def build_unified_data():
    """Fetch unified data directly from the active database with fallback to raw files."""
    try:
        from onion_explorer.database import get_database
        database = get_database()
        return database.get_unified_data()
    except Exception as e:
        log.error(f"Error fetching data from database: {e}. Falling back to raw files.")
        return build_unified_data_from_files()


def sync_data_to_database():
    """Sync all raw files currently stored in DATA_DIR into the database in a single batch."""
    log.info("Synchronizing raw feeds to database...")
    try:
        from onion_explorer.database import get_database
        database = get_database()
        
        # Build unified data from files
        data = build_unified_data_from_files()
        
        batch = []
        
        # Collect Forums/Groups
        for key, val in data.get("forums_groups", {}).items():
            batch.append({
                "key": key,
                "name": val.get("name", key),
                "sector": "forums_groups",
                "type_val": val.get("type", "group"),
                "sources": val.get("sources", []),
                "urls": val.get("urls", []),
                "stats": val.get("stats", {})
            })
            
        # Collect Markets
        for key, val in data.get("markets", {}).items():
            batch.append({
                "key": key,
                "name": val.get("name", key),
                "sector": "markets",
                "type_val": val.get("type", "market"),
                "sources": val.get("sources", []),
                "urls": val.get("urls", []),
                "stats": val.get("stats", {})
            })
            
        # Collect Telegram Links
        for key, val in data.get("telegram_links", {}).items():
            batch.append({
                "key": key,
                "name": val.get("name", key),
                "sector": "telegram_links",
                "type_val": val.get("type", "telegram"),
                "sources": val.get("sources", []),
                "urls": val.get("urls", []),
                "stats": val.get("stats", {})
            })
            
        if batch:
            database.save_entities_batch(batch)
            
        # Save metadata
        database.save_meta(data.get("meta", {}))
        log.info(f"Database sync complete. Synced {len(batch)} entities.")
        log.info("=" * 60)
        log.info("📢 [Sync] All links data stored, ready for scanning!")
        log.info("=" * 60)
        
        # Clean up temporary JSON/CSV cache files to save space and rely on structured DB
        cleanup_raw_data_files()
    except Exception as e:
        log.error(f"Failed to sync data to database: {e}")


def cleanup_raw_data_files():
    """Delete raw JSON feed cache files from data/ directory to preserve cleanliness but keep CSV files."""
    import glob
    log.info("Cleaning up temporary raw JSON feed cache files from data/...")
    patterns = [
        os.path.join(DATA_DIR, "*.json"),
    ]
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            filename = os.path.basename(filepath)
            # EXCLUDE config.json
            if filename in ("config.json",):
                continue
            try:
                os.remove(filepath)
                log.info(f"Deleted raw cache file: {filename}")
            except Exception as e:
                log.warn(f"Failed to delete {filename}: {e}")


def build_stats(data):
    """Dashboard-level stats."""
    fg = data["forums_groups"]
    m = data["markets"]
    tg = data.get("telegram_links", {})

    fg_urls = sum(g["total_urls"] for g in fg.values())
    fg_online = sum(g["online_count"] for g in fg.values())
    fg_offline = sum(g["offline_count"] for g in fg.values())

    m_urls = sum(m["total_urls"] for m in m.values())
    m_online = sum(m["online_count"] for m in m.values())
    m_offline = sum(m["offline_count"] for m in m.values())

    tg_urls = sum(t["total_urls"] for t in tg.values())
    tg_online = sum(t["online_count"] for t in tg.values())
    tg_offline = sum(t["offline_count"] for t in tg.values())

    sources = {}
    for coll in (fg, m, tg):
        for e in coll.values():
            for s in e.get("sources", []):
                sources[s] = sources.get(s, 0) + 1

    return {
        "total_groups": len(fg),
        "total_markets": len(m),
        "total_telegram": len(tg),
        "total_urls": fg_urls + m_urls + tg_urls,
        "total_online": fg_online + m_online + tg_online,
        "total_offline": fg_offline + m_offline + tg_offline,
        "group_urls": fg_urls,
        "group_online": fg_online,
        "market_urls": m_urls,
        "market_online": m_online,
        "telegram_urls": tg_urls,
        "telegram_online": tg_online,
        "sources": sources,
        "meta": data["meta"]
    }


# ═══════════════════════════════════════════════
#  BACKGROUND SCRAPER & LIVE LOGS
# ═══════════════════════════════════════════════

recent_scraper_logs = []

def add_log_entry(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {"time": timestamp, "message": msg, "level": level}
    with state_lock:
        recent_scraper_logs.append(entry)
        if len(recent_scraper_logs) > 80:
            recent_scraper_logs.pop(0)

def log_event(msg, level="INFO"):
    if level == "ERROR":
        log.error(msg)
    elif level == "WARNING":
        log.warning(msg)
    else:
        log.info(msg)
    add_log_entry(msg, level)

def run_scraper(name, scraper_fn):
    """Run a single scraper with error handling and log capture."""
    try:
        log_event(f"🚀 Starting {name} scraper...")
        scraper_fn()
        log_event(f"✅ {name} scraper completed successfully.")
    except Exception as e:
        log_event(f"❌ {name} scraper failed: {e}", level="ERROR")


def run_all_scrapers():
    """Run all feed scrapers using the onion_explorer library."""
    with state_lock:
        scraper_state["is_running"] = True

    log_event("=" * 50)
    log_event("🚀 Starting full darkweb scrape cycle across all sources...")
    start = time.time()
    try:
        # 1. Run GitHubFeed first (instantaneous extraction)
        try:
            run_scraper("GitHubFeed", github_feed.scrape_and_save_github_feeds)
            sync_data_to_database()
        except Exception as e:
            log_event(f"GitHubFeed error: {e}", level="ERROR")

        # 2. Run TelegramChecker (checks telegram invite link validity)
        try:
            run_scraper("TelegramChecker", lambda: telegram_checker.check_all_telegram_links(GITHUB_TELEGRAM_JSON))
            sync_data_to_database()
        except Exception as e:
            log_event(f"TelegramChecker error: {e}", level="ERROR")

        # 3. RansomFeed legacy fallback
        try:
            run_scraper("RansomFeed", ransomfeed.main)
            sync_data_to_database()
        except Exception as e:
            log_event(f"RansomFeed run error: {e}", level="ERROR")

        # 4. RansomLook (async)
        try:
            run_scraper("RansomLook", lambda: asyncio.run(ransomelook.main()))
            sync_data_to_database()
        except Exception as e:
            log_event(f"RansomLook run error: {e}", level="ERROR")

        # 5. RansomwareLive
        try:
            run_scraper("RansomwareLive", ransomelive.main)
            sync_data_to_database()
        except Exception as e:
            log_event(f"RansomwareLive run error: {e}", level="ERROR")

        # 6. WatchGuard
        try:
            run_scraper("WatchGuard", watchguard.main)
            sync_data_to_database()
        except Exception as e:
            log_event(f"WatchGuard run error: {e}", level="ERROR")

        # 7. Library unified export
        try:
            client = ThreatLocationClient()
            locations = client.fetch_all_locations()
            export_to_json(locations, os.path.join(DATA_DIR, "all_threat_locations.json"))
            export_to_csv(locations, os.path.join(DATA_DIR, "all_threat_locations.csv"))
            log_event(f"📦 Unified dataset exported: {len(locations)} threat locations (JSON & CSV)")
        except Exception as e:
            log_event(f"Unified export error: {e}", level="ERROR")

        elapsed = time.time() - start
        log_event(f"✨ All darkweb scrapers finished successfully in {elapsed:.1f} seconds!")
        log_event("=" * 50)

        with state_lock:
            scraper_state["last_scrape"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            scraper_state["last_error"] = None
            scraper_state["scrape_count"] += 1

    except Exception as e:
        log.error(f"Scraper cycle error: {e}")
        with state_lock:
            scraper_state["last_error"] = str(e)
    finally:
        with state_lock:
            scraper_state["is_running"] = False


def apscheduler_scraper_job():
    """Wrapper for scheduled scraper runs to calculate the next execution time and run scrapers."""
    log.info("APScheduler triggered scraper run...")
    run_all_scrapers()
    
    interval = scraper_state["interval_minutes"]
    next_time = datetime.now() + timedelta(minutes=interval)
    with state_lock:
        scraper_state["next_scrape"] = next_time.strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"Scheduled run completed. Next run scheduled at {next_time.strftime('%Y-%m-%d %H:%M:%S')}")


def start_background_scraper():
    """Initialize and start the APScheduler background thread."""
    has_existing_data = False
    try:
        data = build_unified_data()
        if data.get("forums_groups") or data.get("markets") or data.get("telegram_links"):
            # Check that there's actual data entries
            if len(data.get("forums_groups", {})) > 0 or len(data.get("markets", {})) > 0 or len(data.get("telegram_links", {})) > 0:
                has_existing_data = True
    except Exception as e:
        log.warning(f"Failed to check existing database records: {e}")

    with state_lock:
        scraper_state["next_scrape"] = "Calculating..."
        for fp in [RANSOMFEED_URLS_JSON, RANSOMLOOK_GROUPS_JSON, RANSOMWARE_LIVE_JSON]:
            mt = get_file_mtime(fp)
            if mt:
                scraper_state["last_scrape"] = mt
                break

    # Schedule the interval job
    scheduler.add_job(
        func=apscheduler_scraper_job,
        trigger="interval",
        minutes=scraper_state["interval_minutes"],
        id="scraper_job",
        replace_existing=True
    )
    scheduler.start()
    
    if has_existing_data:
        # DB has data, skip initial run and schedule next run time based on interval
        next_time = datetime.now() + timedelta(minutes=scraper_state["interval_minutes"])
        with state_lock:
            scraper_state["next_scrape"] = next_time.strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"Database has existing threat intelligence data. Skipping initial startup scrape. Next run scheduled at {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        # DB is empty, trigger an immediate initial run to scrape feeds
        with state_lock:
            scraper_state["next_scrape"] = "Immediate scrape in progress..."
        init_thread = threading.Thread(target=apscheduler_scraper_job, daemon=True, name="InitialScrape")
        init_thread.start()
        log.info(f"Database is empty. Triggering immediate startup scrape in background...")


# ═══════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════

@app.route("/")
def index():
    return jsonify({
        "name": "OnionExplorer Threat Intelligence API",
        "status": "active",
        "version": "2.0.0",
        "endpoints": {
            "unified_data": "/api/data",
            "statistics": "/api/stats",
            "logs_stream": "/api/scraper/logs",
            "scraper_status": "/api/scraper/status",
            "scraper_trigger": "/api/scraper/run",
            "scraper_config": "/api/config",
            "screenshot_check": "/api/screenshot/check"
        }
    })


@app.route("/api/data")
def api_data():
    """Full unified dataset with groups and markets separated."""
    data = build_unified_data()
    return jsonify(data)


def sanitize_csv_cell(value):
    """Sanitize cells to prevent CSV injection vulnerabilities by escaping symbols (=, +, -, @)."""
    if value is None:
        return ""
    val_str = str(value)
    if val_str and val_str[0] in ('=', '+', '-', '@'):
        return f"'{val_str}"
    return val_str


@app.route("/api/export/csv")
def api_export_csv():
    """Export links to professional CSV format with injection sanitizer and metadata headers."""
    sector = request.args.get("sector", "forums_groups")
    status_filter = request.args.get("status", "all")
    source_filter = request.args.get("source", "all")
    data = build_unified_data()
    
    if sector == "all_sectors":
        collections_to_export = [
            (data.get("forums_groups", {}), "Group"),
            (data.get("markets", {}), "Market"),
            (data.get("telegram_links", {}), "Telegram")
        ]
        label = "All Sectors"
    elif sector == "markets":
        collections_to_export = [(data.get("markets", {}), "Market")]
        label = "Market"
    elif sector == "telegram_links":
        collections_to_export = [(data.get("telegram_links", {}), "Telegram")]
        label = "Telegram"
    else:
        collections_to_export = [(data.get("forums_groups", {}), "Group")]
        label = "Group"

    rows = []
    total_records = 0
    online_count = 0

    for target_data, sec_label in collections_to_export:
        for key, val in target_data.items():
            for url_info in val.get("urls", []):
                url_status = url_info.get("status", "Unknown")
                is_online = (url_status == "Online")
                url_source = url_info.get("source", "")
                
                # Apply status filter
                if status_filter == "online" and not is_online:
                    continue
                if status_filter == "offline" and is_online:
                    continue
                # Apply source filter
                if source_filter != "all" and url_source != source_filter:
                    continue
                    
                total_records += 1
                if is_online:
                    online_count += 1

                rows.append((
                    sanitize_csv_cell(val.get("name", key)),
                    sanitize_csv_cell(sec_label),
                    sanitize_csv_cell(url_info.get("url", "")),
                    sanitize_csv_cell(url_status),
                    sanitize_csv_cell(url_source),
                    sanitize_csv_cell(url_info.get("last_visit", "") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                ))

    si = io.StringIO()
    cw = csv.writer(si)
    
    # 1. First line metadata header for corporate audit logs
    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    cw.writerow([f"# OnionExplorer Threat Intelligence Export - Generated: {timestamp_str} | Total: {total_records} | Online: {online_count}"])
    
    # 2. Column Headers
    cw.writerow(["Entity Name", "Sector", "Onion URL/Link", "Status", "Source Feed File", "Last Updated (UTC)"])
    
    # 3. Data Rows
    cw.writerows(rows)
                
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=export_{sector}_{status_filter}_{source_filter}.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@app.route("/api/export/markdown")
def api_export_markdown():
    """Export threat links to a professional, structured Markdown report."""
    sector = request.args.get("sector", "forums_groups")
    status_filter = request.args.get("status", "all")
    source_filter = request.args.get("source", "all")
    data = build_unified_data()
    
    if sector == "all_sectors":
        collections_to_export = [
            (data.get("forums_groups", {}), "Group"),
            (data.get("markets", {}), "Market"),
            (data.get("telegram_links", {}), "Telegram")
        ]
        label = "All Sectors"
    elif sector == "markets":
        collections_to_export = [(data.get("markets", {}), "Market")]
        label = "Market"
    elif sector == "telegram_links":
        collections_to_export = [(data.get("telegram_links", {}), "Telegram")]
        label = "Telegram"
    else:
        collections_to_export = [(data.get("forums_groups", {}), "Group")]
        label = "Group"

    total_entities = 0
    total_urls = 0
    online_count = 0
    offline_count = 0
    
    rows = []
    for target_data, sec_label in collections_to_export:
        for key, val in target_data.items():
            entity_has_matched_urls = False
            for url_info in val.get("urls", []):
                url_status = url_info.get("status", "Unknown")
                is_online = (url_status == "Online")
                url_source = url_info.get("source", "")
                
                # Apply status filter
                if status_filter == "online" and not is_online:
                    continue
                if status_filter == "offline" and is_online:
                    continue
                # Apply source filter
                if source_filter != "all" and url_source != source_filter:
                    continue
                    
                entity_has_matched_urls = True
                total_urls += 1
                if is_online:
                    online_count += 1
                else:
                    offline_count += 1
                    
                rows.append({
                    "name": val.get("name", key),
                    "sector": sec_label,
                    "url": url_info.get("url", ""),
                    "status": "🟢 Online" if is_online else "🔴 Offline",
                    "source": url_source,
                    "last_visit": url_info.get("last_visit", "") or "N/A"
                })
            if entity_has_matched_urls:
                total_entities += 1

    md = []
    md.append("# OnionExplorer Threat Intelligence Report")
    md.append(f"**Generated on (UTC)**: `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}`")
    md.append(f"**Filter Criteria**:")
    md.append(f"- Sector Filter: `{label}`")
    md.append(f"- Status Filter: `{status_filter.capitalize()}`")
    md.append(f"- Source Filter: `{source_filter}`")
    md.append("\n---\n")
    md.append("## Executive Summary")
    md.append("| Metric | Count |")
    md.append("| :--- | :--- |")
    md.append(f"| Total Matching Entities | {total_entities} |")
    md.append(f"| Total Matching URLs | {total_urls} |")
    md.append(f"| Active (Online) Links | {online_count} |")
    md.append(f"| Inactive (Offline) Links | {offline_count} |")
    md.append("\n---\n")
    md.append("## Detailed Threat Links")
    md.append("| Entity Name | Sector | Onion URL / Link | Status | Source Feed | Last Visit (UTC) |")
    md.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    
    for r in rows:
        md.append(f"| {r['name']} | {r['sector']} | `{r['url']}` | {r['status']} | `{r['source']}` | {r['last_visit']} |")
        
    md.append("\n---\n")
    md.append("> **Disclaimer**: This intelligence report is generated automatically by OnionExplorer for corporate asset security monitoring and internal analysis. Direct access to Dark Web resources carries risk. Handle with caution.")
    
    output = make_response("\n".join(md))
    output.headers["Content-Disposition"] = f"attachment; filename=export_{sector}_{status_filter}_{source_filter}.md"
    output.headers["Content-type"] = "text/markdown; charset=utf-8"
    return output


    return output


@app.route("/api/export/html")
def api_export_html():
    """Export threat intelligence data to a self-contained HTML report with embedded Base64 screenshots."""
    sector = request.args.get("sector", "forums_groups")
    status_filter = request.args.get("status", "all")
    source_filter = request.args.get("source", "all")
    data = build_unified_data()
    
    if sector == "all_sectors":
        collections_list = [
            (data.get("forums_groups", {}), "Group"),
            (data.get("markets", {}), "Market"),
            (data.get("telegram_links", {}), "Telegram")
        ]
        scope_label = "All Sectors"
    elif sector == "markets":
        collections_list = [(data.get("markets", {}), "Market")]
        scope_label = "Markets"
    elif sector == "telegram_links":
        collections_list = [(data.get("telegram_links", {}), "Telegram")]
        scope_label = "Telegram Links"
    else:
        collections_list = [(data.get("forums_groups", {}), "Forums & Groups")]
        scope_label = "Forums & Groups"

    rows = []
    for target_data, sec_label in collections_list:
        for key, val in target_data.items():
            for url_info in val.get("urls", []):
                url_status = url_info.get("status", "Unknown")
                is_online = (url_status == "Online" or url_status == "Up")
                url_source = url_info.get("source", "")
                # Only include Online / Up links in HTML report
                if not is_online:
                    continue
                if source_filter != "all" and url_source != source_filter:
                    continue
                    
                # Convert screenshot image to base64 if present
                img_src = None
                raw_screenshot = url_info.get("screenshot")
                if raw_screenshot:
                    filename = os.path.basename(raw_screenshot)
                    from onion_explorer.screenshot_worker import SCREENSHOTS_DIR
                    filepath = os.path.join(SCREENSHOTS_DIR, filename)
                    if os.path.exists(filepath):
                        try:
                            import base64
                            with open(filepath, "rb") as image_file:
                                b64 = base64.b64encode(image_file.read()).decode("utf-8")
                                img_src = f"data:image/png;base64,{b64}"
                        except Exception as b64_err:
                            log.error(f"Base64 conversion failed for {filepath}: {b64_err}")
                
                rows.append({
                    "entity_name": val.get("name", key),
                    "sector": sec_label,
                    "url": url_info.get("url", ""),
                    "status": "Online" if is_online else "Offline",
                    "source": url_source,
                    "last_visit": url_info.get("last_visit", "") or "N/A",
                    "screenshot_b64": img_src
                })

    # Sort rows alphabetically by entity/group name (A-Z)
    rows.sort(key=lambda x: x["entity_name"].lower())

    html_content = export_to_html(rows, scope_label, status_filter, source_filter)
    
    output = make_response(html_content)
    output.headers["Content-Disposition"] = f"attachment; filename=export_{sector}_{status_filter}_{source_filter}.html"
    output.headers["Content-type"] = "text/html; charset=utf-8"
    return output


@app.route("/api/stats")
def api_stats():
    """Summary statistics."""
    data = build_unified_data()
    return jsonify(build_stats(data))


@app.route("/api/group/<name>")
def api_group(name):
    """Single group details."""
    data = build_unified_data()
    key = name.lower().strip()
    if key in data["forums_groups"]:
        return jsonify(data["forums_groups"][key])
    if key in data["markets"]:
        return jsonify(data["markets"][key])
    if key in data["telegram_links"]:
        return jsonify(data["telegram_links"][key])
    return jsonify({"error": f"'{name}' not found"}), 404


@app.route("/api/scraper/status")
def api_scraper_status():
    """Return scraper state."""
    with state_lock:
        return jsonify(scraper_state)


@app.route("/api/scraper/logs")
def api_scraper_logs():
    """Return recent in-memory scraper logs."""
    with state_lock:
        return jsonify(recent_scraper_logs)


@app.route("/api/scraper/run", methods=["POST"])
@app.route("/api/scraper/trigger", methods=["POST"])
def api_scraper_run():
    """Manually trigger a scrape."""
    with state_lock:
        if scraper_state["is_running"]:
            return jsonify({"status": "already_running"}), 409

    # Run in a thread to not block the response
    thread = threading.Thread(target=run_all_scrapers, daemon=True, name="ManualScrape")
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Get or update scraper configuration."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        interval = data.get("interval_minutes")
        if interval and isinstance(interval, (int, float)) and interval >= 1:
            with state_lock:
                scraper_state["interval_minutes"] = int(interval)
                try:
                    scheduler.reschedule_job("scraper_job", trigger="interval", minutes=int(interval))
                except Exception as scheduler_err:
                    log.error(f"Failed to reschedule scraper job: {scheduler_err}")
            return jsonify({"status": "updated", "interval_minutes": int(interval)})
        return jsonify({"error": "Invalid interval"}), 400

    with state_lock:
        return jsonify({
            "interval_minutes": scraper_state["interval_minutes"]
        })


@app.route("/api/screenshot/check", methods=["POST"])
def api_screenshot_check():
    """Manually trigger a check & screenshot for a single URL."""
    req = request.get_json(silent=True) or {}
    url = req.get("url")
    entity_key = req.get("entity_key")
    if not url or not entity_key:
        return jsonify({"error": "Missing url or entity_key"}), 400

    if "t.me" in url.lower() or "telegram.me" in url.lower():
        return jsonify({"error": "Telegram links cannot be screenshot verified."}), 400

    queue_url_for_screenshot(entity_key, url, force=True)
    start_screenshot_worker()
    return jsonify({"status": "queued"})


@app.route("/api/screenshot/scan_all_online", methods=["POST"])
def api_screenshot_scan_all_online():
    """Manually trigger check & screenshot verification for all online onion URLs."""
    data = build_unified_data()
    queued_count = 0
    
    # Combine Forums/Groups and Markets into a single unified list
    all_entities = []
    for key, val in data.get("forums_groups", {}).items():
        if val.get("sector") == "telegram_links" or "telegram" in key.lower():
            continue
        all_entities.append((key, val))
    for key, val in data.get("markets", {}).items():
        if val.get("sector") == "telegram_links" or "telegram" in key.lower():
            continue
        all_entities.append((key, val))
        
    # Sort the unified list alphabetically by group/entity name
    sorted_entities = sorted(
        all_entities,
        key=lambda x: x[1].get("name", x[0]).lower()
    )
    
    # Queue them in unified alphabetical order
    for key, val in sorted_entities:
        for u in val.get("urls", []):
            url_str = u.get("url", "")
            if "t.me" in url_str.lower() or "telegram.me" in url_str.lower():
                continue
            if u.get("status") == "Online" or u.get("status") == "Up":
                queue_url_for_screenshot(key, url_str, force=True)
                queued_count += 1
                
    log.info(f"⚡ [Scan All Online] Queued {queued_count} online URLs alphabetically for screenshot check.")
    if queued_count > 0:
        start_screenshot_worker()
    return jsonify({"status": "queued", "count": queued_count})


@app.route("/api/screenshot/status", methods=["GET"])
def api_screenshot_status():
    """Return screenshot worker running and queue size status."""
    return jsonify(get_screenshot_worker_status())


@app.route("/api/screenshot/pause", methods=["POST"])
def api_screenshot_pause():
    """Stop/Pause the background screenshot worker thread."""
    stop_screenshot_worker()
    return jsonify({"status": "paused", "running": False})


@app.route("/api/screenshot/resume", methods=["POST"])
def api_screenshot_resume():
    """Start/Resume the background screenshot worker thread."""
    start_screenshot_worker()
    return jsonify({"status": "resumed", "running": True})


@app.route("/api/scan/reset", methods=["POST"])
def api_scan_reset():
    """Reset all scanned statuses back to 'Not scanned yet', clear screenshots, and empty queue."""
    try:
        from onion_explorer.database import get_database
        from onion_explorer.screenshot_worker import reset_screenshot_worker
        db = get_database()
        db.reset_all_scanned_statuses()
        reset_screenshot_worker()
        log.info("🧹 [Reset] All scanned statuses, screenshots, and task queue have been reset.")
        return jsonify({"status": "success", "message": "All scanned statuses reset."})
    except Exception as e:
        log.error(f"Reset scan error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/static/screenshots/<path:filename>")
def serve_screenshot(filename):
    """Serve saved screenshot PNG files from static/screenshots directory."""
    screenshots_dir = os.path.join(BASE_DIR, "static", "screenshots")
    return send_from_directory(screenshots_dir, filename)


@app.route("/api/url/analyst_update", methods=["POST"])
def api_url_analyst_update():
    """Manually update analyst annotations for a specific location link."""
    req = request.get_json(silent=True) or {}
    entity_key = req.get("entity_key")
    url = req.get("url")
    analyst_working = req.get("analyst_working", False)
    analyst_notes = req.get("analyst_notes", "")
    
    if not entity_key or not url:
        return jsonify({"error": "Missing entity_key or url"}), 400
        
    try:
        from onion_explorer.database import get_database
        db = get_database()
        db.update_analyst_annotations(entity_key, url, analyst_working, analyst_notes)
        return jsonify({"status": "success"})
    except Exception as e:
        log.error(f"Failed to update analyst annotations: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/url/status_update", methods=["POST"])
def api_url_status_update():
    """Manually override a location URL's status."""
    req = request.get_json(silent=True) or {}
    entity_key = req.get("entity_key")
    url = req.get("url")
    status = req.get("status")
    
    if not entity_key or not url or not status:
        return jsonify({"error": "Missing entity_key, url or status"}), 400
        
    try:
        from onion_explorer.database import get_database
        db = get_database()
        db.update_location_status(entity_key, url, status)
        log.info(f"💾 [Manual Override] Analyst set status of {url} (Group: {entity_key}) to {status}")
        return jsonify({"status": "success"})
    except Exception as e:
        log.error(f"Failed to update location status override: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("OnionExplorer Dashboard")
    log.info(f"  Data directory : {DATA_DIR}")
    log.info(f"  Scrape interval: {SCRAPE_INTERVAL_MINUTES} minutes")
    log.info(f"  Server         : http://localhost:5000")
    log.info("=" * 50)

    # Start background screenshot worker
    from onion_explorer.screenshot_worker import register_log_callback
    register_log_callback(add_log_entry)
    start_screenshot_worker()

    # Sync raw JSON data to database on startup
    try:
        sync_data_to_database()
    except Exception as e:
        log.error(f"Initial database sync error: {e}")

    # Start background scraper
    start_background_scraper()

    # Start Flask
    app.run(debug=False, host="0.0.0.0", port=5000)
