#!/usr/bin/env python3
import os, glob, html, importlib.util
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

HOME = os.path.expanduser("~")
LOG_FILE = os.path.join(HOME, "sat_tracker.log")
CFG_FILE = os.path.join(HOME, "config.py")

def get_station_info():
    rx_dirs = [os.path.join(HOME, "qsstv", "rx_sstv")]
    sdr_summary = ["Default SDR (2m VHF)"]
    if os.path.exists(CFG_FILE):
        try:
            spec = importlib.util.spec_from_file_location("user_cfg", CFG_FILE)
            cfg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cfg)
            sdrs = getattr(cfg, "SDR_CONFIGS", [])
            if sdrs:
                rx_dirs = [os.path.expanduser(s["rx_dir"]) for s in sdrs]
                sdr_summary = [f"SDR #{s['index']}: {s['sdr_type']} ({s['label']})" for s in sdrs]
        except Exception:
            pass
    return rx_dirs, sdr_summary

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path)
        rx_dirs, sdr_summary = get_station_info()
        if path.startswith("/img/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[1].isdigit():
                dir_idx = int(parts[1])
                fname = os.path.basename(parts[2])
                if 0 <= dir_idx < len(rx_dirs):
                    fpath = os.path.join(rx_dirs[dir_idx], fname)
                    if os.path.isfile(fpath):
                        ctype = "image/png" if fname.lower().endswith(".png") else "image/jpeg"
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.end_headers()
                        self.wfile.write(open(fpath, "rb").read())
                        return
            self.send_error(404)
            return
        logs = open(LOG_FILE, errors="ignore").read()[-3500:] if os.path.exists(LOG_FILE) else "Waiting for sat_tracker.log..."
        logs = html.escape("\n".join(logs.splitlines()[-25:]))
        all_imgs = []
        for idx, d in enumerate(rx_dirs):
            for p in glob.glob(os.path.join(d, "*.*")):
                if p.lower().endswith((".png", ".jpg", ".jpeg")):
                    all_imgs.append((os.path.getmtime(p), idx, p))
        all_imgs.sort(reverse=True)
        cards = []
        for mtime, idx, p in all_imgs:
            fname = os.path.basename(p)
            ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            url = f"/img/{idx}/{fname}"
            cards.append(f'<div class="card"><a href="{url}" target="_blank"><img src="{url}" loading="lazy"></a><div class="meta"><strong>{html.escape(fname)}</strong><br><span>{ts} (SDR #{idx})</span></div></div>')
        gallery = "".join(cards) if cards else '<p class="empty">No SSTV frames decoded yet — station is armed and waiting for the next pass.</p>'
        badges = "".join(f'<span class="badge">{html.escape(s)}</span>' for s in sdr_summary)
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><title>Auto-SSTV Station</title><style>body{{font-family:system-ui,sans-serif;background:#0f172a;color:#f8fafc;margin:0;padding:24px}}.wrap{{max-width:1180px;margin:0 auto}}h1{{margin:0 0 8px;font-size:1.6rem}}h2{{margin:24px 0 12px;font-size:1.15rem;color:#93c5fd}}.badges{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}}.badge{{background:#1e293b;border:1px solid #334155;padding:4px 10px;border-radius:999px;font-size:.85rem;color:#38bdf8}}pre{{background:#1e293b;border:1px solid #334155;padding:14px;border-radius:10px;overflow-x:auto;font-size:.85rem;line-height:1.45;color:#e2e8f0}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}}.card{{background:#1e293b;border:1px solid #334155;border-radius:10px;overflow:hidden}}.card img{{width:100%;display:block;aspect-ratio:4/3;object-fit:cover;background:#020617}}.meta{{padding:10px 12px;font-size:.85rem}}.meta span{{color:#94a3b8;font-size:.78rem}}.empty{{background:#1e293b;padding:20px;border-radius:10px;color:#94a3b8}}</style></head><body><div class="wrap"><h1>Auto-SSTV Station — Live Dashboard</h1><div class="badges">{badges}<span class="badge">Auto-Refresh: 15s</span></div><h2>Live Tracker Log</h2><pre>{logs}</pre><h2>Decoded SSTV Gallery ({len(all_imgs)})</h2><div class="grid">{gallery}</div></div></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())
    def log_message(self, *args): pass

if __name__ == "__main__":
    print("Auto-SSTV Web Dashboard running on http://0.0.0")
    HTTPServer(("0.0.0.0", 8080), DashboardHandler).serve_forever()
