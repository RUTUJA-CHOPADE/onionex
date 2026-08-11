import os
import re
import time
import socket
import logging
import hashlib
import threading
import shutil
from queue import Queue, Empty
from typing import Dict, Any

from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.common.exceptions import WebDriverException
from webdriver_manager.firefox import GeckoDriverManager

from onion_explorer.database import get_database

logger = logging.getLogger("OnionExplorer.ScreenshotWorker")

# Logging callback to route progress to Flask UI log console without circular dependencies
log_callback = None

def register_log_callback(cb):
    global log_callback
    log_callback = cb

def log_worker_event(msg, level="INFO"):
    if level == "ERROR":
        logger.error(msg)
    elif level == "WARNING":
        logger.warning(msg)
    else:
        logger.info(msg)
        
    if log_callback:
        log_callback(msg, level)

# Directory setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
SCREENSHOTS_DIR = os.path.join(STATIC_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

# SOCKS5 Tor default ports
TOR_HOST = "127.0.0.1"
TOR_PORT = 9050

# Worker queue & task tracking
task_queue = Queue()
active_tasks = set()
tasks_lock = threading.Lock()
worker_thread = None
running = True

def is_tor_active() -> bool:
    """Test if SOCKS5 Tor proxy is listening on localhost."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect((TOR_HOST, TOR_PORT))
        s.close()
        return True
    except Exception:
        return False

# Set to False to open visible browser windows, or True to run silently in the background
HEADLESS = False

def get_url_md5(url: str) -> str:
    """Compute MD5 hex hash of a URL."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()

def make_screenshot_driver(use_tor: bool = True) -> webdriver.Firefox:
    """Instantiate a Firefox web driver with optional SOCKS5 proxy configuration."""
    # Ubuntu Snap Firefox sandbox fix: redirect profile creation directory from /tmp to workspace writeable data/tmp
    custom_tmp = os.path.join(DATA_DIR, "tmp")
    os.makedirs(custom_tmp, exist_ok=True)
    os.environ["TMPDIR"] = custom_tmp

    opts = FirefoxOptions()
    if HEADLESS:
        opts.add_argument("-headless")
    
    # Auto-detect Firefox binary path on Linux/Ubuntu (including Snap installations)
    firefox_path = os.environ.get("FIREFOX_BIN") or shutil.which("firefox") or shutil.which("firefox-esr")
    if not firefox_path:
        for p in ["/usr/bin/firefox", "/snap/bin/firefox", "/usr/lib/firefox/firefox"]:
            if os.path.exists(p):
                firefox_path = p
                break
    if firefox_path:
        opts.binary_location = firefox_path

    # Configure user-agent in Firefox preferences
    opts.set_preference("general.useragent.override", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0")
    
    if use_tor:
        log_worker_event(f"Routing Firefox browser automation traffic via Tor SOCKS5 proxy ({TOR_HOST}:{TOR_PORT})...")
        opts.set_preference("network.proxy.type", 1)
        opts.set_preference("network.proxy.socks", TOR_HOST)
        opts.set_preference("network.proxy.socks_port", TOR_PORT)
        opts.set_preference("network.proxy.socks_version", 5)
        opts.set_preference("network.proxy.socks_remote_dns", True) # Force DNS resolution via Tor

    try:
        service = FirefoxService(GeckoDriverManager().install())
        driver = webdriver.Firefox(service=service, options=opts)
    except Exception as gdm_err:
        logger.warning(f"GeckoDriverManager install failed: {gdm_err}. Attempting default system geckodriver path fallback.")
        driver = webdriver.Firefox(options=opts)
    driver.set_page_load_timeout(60) # Long timeout to allow slow Tor loading
    return driver

def capture_screenshot_task(entity_key: str, url: str) -> bool:
    """Loads a URL via Firefox driver, takes screenshot, and updates DB status."""
    use_tor = is_tor_active()
    if not use_tor:
        log_worker_event(f"⚠️ Tor SOCKS5 proxy not detected. Verification will fallback to direct connection.", level="WARNING")

    md5_hash = get_url_md5(url)
    filename = f"{md5_hash}.png"
    save_path = os.path.join(SCREENSHOTS_DIR, filename)

    driver = None
    success = False
    status_val = "Offline"

    try:
        driver = make_screenshot_driver(use_tor=use_tor)
        log_worker_event(f"🔍 [Scan] Launching Firefox window for: {url} (Group: {entity_key})")
        driver.get(url)
        
        # Wait exactly 20 seconds for dynamic content to load completely
        log_worker_event(f"⏳ [Scan] Loaded URL. Waiting 20 seconds for settling: {url} (Group: {entity_key})")
        time.sleep(20)
        
        # Save screenshot
        driver.save_screenshot(save_path)
        log_worker_event(f"📸 [Scan] Screenshot saved successfully: static/screenshots/{filename}! (Group: {entity_key})")
        success = True
        status_val = "Online"
    except WebDriverException as wde:
        log_worker_event(f"❌ [Scan] Firefox connection error for {url} (Group: {entity_key}): {wde}", level="ERROR")
        # Delete stale screenshot on load failure
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
    except Exception as e:
        log_worker_event(f"❌ [Scan] Error capturing {url} (Group: {entity_key}): {e}", level="ERROR")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # Save to database
    try:
        db = get_database()
        db.update_location_screenshot(entity_key, url, filename if success else None, status_val)
        log_worker_event(f"💾 [Scan] Updated database status to {status_val} for {url} (Group: {entity_key})")
    except Exception as dbe:
        log_worker_event(f"❌ [Scan] Failed to update database status: {dbe}", level="ERROR")

    return success

def save_queue_to_db():
    try:
        db = get_database()
        tasks = list(task_queue.queue)
        db.save_screenshot_queue(tasks)
    except Exception as e:
        logger.error(f"Failed to persist queue to DB: {e}")

def load_queue_from_db():
    try:
        db = get_database()
        tasks = db.load_screenshot_queue()
        with tasks_lock:
            for t in tasks:
                task_id = f"{t['entity_key']}:{t['url']}"
                if task_id not in active_tasks:
                    active_tasks.add(task_id)
                    task_queue.put(t)
        if tasks:
            log_worker_event(f"💾 Loaded and restored {len(tasks)} pending screenshot tasks from database.")
    except Exception as e:
        logger.error(f"Failed to load queue from DB: {e}")
def reset_screenshot_worker():
    """Clears in-memory screenshot queue and deletes saved PNG screenshot files."""
    global active_tasks, task_queue
    with tasks_lock:
        active_tasks.clear()
        while not task_queue.empty():
            try:
                task_queue.get_nowait()
                task_queue.task_done()
            except Exception:
                break
    import glob
    for filepath in glob.glob(os.path.join(SCREENSHOTS_DIR, "*.png")):
        try:
            os.remove(filepath)
        except Exception:
            pass
    log_worker_event("🧹 Screenshot worker memory queue and screenshot image cache reset.")


def queue_url_for_screenshot(entity_key: str, url: str, force: bool = False):
    """Adds a location URL to the verification and screenshot task queue."""
    if not url or not url.startswith("http"):
        return

    # Check and reject Telegram links
    if "t.me" in url.lower() or "telegram.me" in url.lower():
        return

    with tasks_lock:
        task_id = f"{entity_key}:{url}"
        if task_id in active_tasks and not force:
            logger.debug(f"Task already in queue or processing: {task_id}")
            return
        active_tasks.add(task_id)
        task_queue.put({"entity_key": entity_key, "url": url})
        save_queue_to_db()
        log_worker_event(f"📥 [Screenshot Queue] Added task: {url}")

def worker_loop():
    """Background execution loop processing tasks sequentially."""
    global running
    log_worker_event("📟 Screenshot verification worker thread started.")
    
    while running:
        try:
            task = task_queue.get(timeout=2)
            start_time = time.time()
            entity_key = task["entity_key"]
            url = task["url"]
            save_queue_to_db()
            
            try:
                capture_screenshot_task(entity_key, url)
            except Exception as loop_err:
                log_worker_event(f"Error executing screenshot task: {loop_err}", level="ERROR")
            finally:
                with tasks_lock:
                    task_id = f"{entity_key}:{url}"
                    active_tasks.discard(task_id)
                task_queue.task_done()
                save_queue_to_db()
                
            # Adaptive rate limit scans to strict 1-minute interval per link
            if running:
                elapsed = time.time() - start_time
                remaining_sleep = max(0.0, 60.0 - elapsed)
                if remaining_sleep > 0:
                    log_worker_event(f"⏳ Scan completed in {elapsed:.1f}s. Adaptive sleep for {remaining_sleep:.1f}s before next scan.")
                    sleep_seconds = int(remaining_sleep)
                    fractional_sleep = remaining_sleep - sleep_seconds
                    if fractional_sleep > 0:
                        time.sleep(fractional_sleep)
                    for _ in range(sleep_seconds):
                        if not running:
                            break
                        time.sleep(1)
                else:
                    log_worker_event(f"⏳ Scan completed in {elapsed:.1f}s (exceeded 60s). Proceeding to next scan immediately.")
        except Empty:
            continue
        except Exception as e:
            log_worker_event(f"Screenshot worker loop error: {e}", level="ERROR")
            time.sleep(2)

def start_screenshot_worker():
    """Initializes and runs the screenshot queue worker thread."""
    global worker_thread, running
    running = True
    # Restore queue first
    load_queue_from_db()
    if worker_thread is None or not worker_thread.is_alive():
        worker_thread = threading.Thread(target=worker_loop, daemon=True, name="ScreenshotWorker")
        worker_thread.start()
        log_worker_event("✨ Screenshot worker thread launched.")

def stop_screenshot_worker():
    """Stops the screenshot queue worker thread gracefully."""
    global running, worker_thread
    running = False
    if worker_thread:
        worker_thread.join(timeout=5)
        worker_thread = None
        log_worker_event("🛑 Screenshot worker thread stopped.")

def get_screenshot_worker_status() -> dict:
    global worker_thread, running
    return {
        "running": bool(worker_thread and worker_thread.is_alive() and running),
        "queue_size": task_queue.qsize()
    }
