#!/usr/bin/env python3
"""
onion — CLI entry point for OnionExplorer
Usage:
    onion serve
"""
import sys
import os
# Inject the root directory containing this file to python's import path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import signal
import subprocess
import threading
import time
from main import app, start_background_scraper, sync_data_to_database, add_log_entry
from onion_explorer.screenshot_worker import start_screenshot_worker, stop_screenshot_worker, register_log_callback
import logging

def serve():
    log = logging.getLogger("OnionExplorer")
    
    backend_host = os.environ.get("HOST", "0.0.0.0")
    backend_port = os.environ.get("PORT", "5000")
    frontend_host = os.environ.get("FRONTEND_HOST", "0.0.0.0")
    frontend_port = os.environ.get("FRONTEND_PORT", "5173")
    frontend_domain = os.environ.get("FRONTEND_DOMAIN", "localhost")

    log.info("=" * 60)
    log.info("🧅 OnionExplorer — Threat Intelligence System")
    log.info(f"  Backend REST API : http://{backend_host}:{backend_port}")
    log.info(f"  Frontend Console : http://{frontend_domain}:{frontend_port}")
    log.info("=" * 60)

    try:
        sync_data_to_database()
    except Exception as e:
        log.error(f"Initial database sync error: {e}")

    # Launch SvelteKit dev server in the background
    frontend_proc = None
    def start_frontend():
        nonlocal frontend_proc
        try:
            # We redirect SvelteKit stdout/stderr to a log file for transparency
            os.makedirs(os.path.join("data", "logs"), exist_ok=True)
            frontend_log_path = os.path.join("data", "logs", "frontend.log")
            
            with open(frontend_log_path, "a", encoding="utf-8") as f_log:
                f_log.write(f"\n--- Starting SvelteKit Dev Server at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                frontend_proc = subprocess.Popen(
                    "npm run dev",
                    shell=True,
                    cwd="frontend",
                    stdout=f_log,
                    stderr=f_log,
                    preexec_fn=None if os.name == 'nt' else os.setsid
                )
            log.info(f"✨ SvelteKit Dev Server started on port {frontend_port} (http://{frontend_domain}:{frontend_port}).")
            log.info(f"  💡 Subprocess logs are saved in data/logs/frontend.log")
        except Exception as err:
            log.error(f"❌ Failed to launch SvelteKit dev server: {err}")

    t = threading.Thread(target=start_frontend, daemon=True)
    t.start()

    # Register logging callback for the screenshot worker (does not auto-start thread)
    register_log_callback(add_log_entry)

    start_background_scraper()
    
    # Auto-open dashboard in default browser after SvelteKit initializes
    def auto_open_browser():
        time.sleep(2.5)
        
        # Skip auto-opening if running in a headless Linux/Ubuntu environment (e.g., SSH session)
        if os.name != 'nt' and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            log.info("🖥️ Headless Linux environment detected (no DISPLAY). Skipping browser auto-open.")
            return

        log.info("🌐 Automatically opening OnionExplorer dashboard in your browser...")
        import webbrowser
        try:
            webbrowser.open(f"http://{frontend_domain}:{frontend_port}")
        except Exception as err:
            log.warning(f"Could not open web browser: {err}")

    threading.Thread(target=auto_open_browser, daemon=True).start()
    
    try:
        app.run(debug=False, host=backend_host, port=int(backend_port))
    finally:
        # Gracefully stop screenshot worker thread
        stop_screenshot_worker()
        if frontend_proc:
            log.info("🛑 Stopping SvelteKit dev server...")
            try:
                if os.name == 'nt':
                    subprocess.run(f"taskkill /F /T /PID {frontend_proc.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.killpg(os.getpgid(frontend_proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    frontend_proc.terminate()
                except Exception:
                    pass

def reset_data():
    log = logging.getLogger("OnionExplorer")
    log.info("🧹 Performing complete system scan reset...")
    try:
        from onion_explorer.database import get_database
        from onion_explorer.screenshot_worker import reset_screenshot_worker
        db = get_database()
        db.reset_all_scanned_statuses()
        reset_screenshot_worker()
        log.info("✅ All scanned data, screenshots, and task queues have been completely wiped!")
        log.info("   All link statuses reset to 'Not scanned yet' and ready for a fresh scan.")
    except Exception as e:
        log.error(f"Reset failed: {e}")

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "reset":
        reset_data()
    elif cmd == "serve":
        serve()
    else:
        print("Usage:")
        print("  onion serve — Start dashboard and screenshot worker")
        print("  onion reset — Clear all previous scan data and screenshots")
        sys.exit(1)

if __name__ == "__main__":
    main()
