#!/usr/bin/env python3
import os, glob, html, math, time, ephem, urllib.request, importlib.util
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

HOME = os.path.expanduser("~")
LOG_FILE = os.path.join(HOME, "sat_tracker.log")
CFG_FILE = os.path.join(HOME, "config.py")
TLE_DIR = os.path.join(HOME, ".config", "Gpredict", "satdata")
_TLE_CACHE = {"ts": 0, "data": {}}

def load_station():
    lat, lon, alt, min_el = 40.60, 22.90, 50.0, 5.0
    rx_dirs = [os.path.join(HOME, "qsstv", "rx_sstv")]
    sdr_summary = ["Default SDR (2m VHF)"]
    sats = {"25544": {"name": "ISS (ZARYA)", "freq": 145800000}, "59112": {"name": "SONATE-2", "freq": 145880000}, "57172": {"name": "UMKA-1", "freq": 437625000}}
    if os.path.exists(CFG_FILE):
        try:
            spec = importlib.util.spec_from_file_location("user_cfg", CFG_FILE)
            cfg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cfg)
            if getattr(cfg, "LAT", None) is not None: lat = float(cfg.LAT)
            if getattr(cfg, "LON", None) is not None: lon = float(cfg.LON)
            if getattr(cfg, "ALT", None) is not None: alt = float(cfg.ALT)
            if getattr(cfg, "MIN_ELEVATION", None) is not None: min_el = float(cfg.MIN_ELEVATION)
            if getattr(cfg, "SATS", None): sats.update(cfg.SATS)
            sdrs = getattr(cfg, "SDR_CONFIGS", [])
            if sdrs:
                rx_dirs = [os.path.expanduser(s["rx_dir"]) for s in sdrs]
                sdr_summary = [f"SDR #{s['index']}: {s['sdr_type']} ({s['label']})" for s in sdrs]
        except Exception:
            pass
    try:
        import urllib.request, json
        req = urllib.request.urlopen("https://satnogs.org", timeout=4)
        for tx in json.loads(req.read().decode()):
            nid = str(tx.get("norad_cat_id") or "")
            freq = tx.get("downlink_low")
            if nid and freq and nid not in sats:
                sats[nid] = {"name": tx.get("satellite_name") or f"NORAD {nid}", "freq": int(freq)}
    except Exception:
        pass
    return lat, lon, alt, min_el, rx_dirs, sdr_summary, sats

def get_tle(norad_id, name):
    nid_clean = str(norad_id).strip()
    sat_path = os.path.join(TLE_DIR, f"{nid_clean}.sat")
    if os.path.exists(sat_path):
        t1, t2 = None, None
        for line in open(sat_path, errors="ignore"):
            s = line.strip()
            if s.startswith("TLE1="): t1 = s[5:].strip()
            elif s.startswith("TLE2="): t2 = s[5:].strip()
        if t1 and t2:
            return (name, t1, t2)
    now = time.time()
    if nid_clean in _TLE_CACHE["data"] and now - _TLE_CACHE["ts"] < 3600:
        return _TLE_CACHE["data"][nid_clean]
    try:
        req = urllib.request.Request(f"https://celestrak.org{nid_clean}&FORMAT=TLE", headers={"User-Agent": "Mozilla/5.0"})
        raw = [l.strip() for l in urllib.request.urlopen(req, timeout=5).read().decode().splitlines() if l.strip()]
        for i in range(len(raw) - 1):
            if raw[i].startswith("1 ") and raw[i+1].startswith("2 "):
                tle_tuple = (name, raw[i], raw[i+1])
                _TLE_CACHE["data"][nid_clean] = tle_tuple
                _TLE_CACHE["ts"] = now
                return tle_tuple
    except Exception:
        pass
    return None

def get_upcoming_passes(lat, lon, alt, min_el, sats):
    obs = ephem.Observer()
    obs.lat, obs.lon, obs.elevation = str(lat), str(lon), alt
    now_ephem = ephem.now()
    passes = []
    for nid, info in sats.items():
        tle = get_tle(str(nid), info.get("name", f"NORAD {nid}"))
        if not tle: continue
        try:
            sat = ephem.readtle(*tle)
            obs.date = now_ephem
            sat.compute(obs)
            cur_el = math.degrees(sat.alt)
            if cur_el >= min_el:
                passes.append((0, info["name"], info["freq"], "ACTIVE NOW", f"{cur_el:.1f}°", "In Progress", "LIVE"))
                continue
            tr, azr, tt, altt, ts, azs = obs.next_pass(sat)
            max_el = math.degrees(altt)
            if max_el < min_el: continue
            aos_dt = ephem.Date(tr).datetime().replace(tzinfo=timezone.utc).astimezone()
            los_dt = ephem.Date(ts).datetime().replace(tzinfo=timezone.utc).astimezone()
            mins = int((aos_dt - datetime.now().astimezone()).total_seconds() // 60)
            if 0 <= mins <= 1440:
                cd = f"in {mins // 60}h {mins % 60}m" if mins >= 60 else f"in {mins}m"
                passes.append((aos_dt.timestamp(), info["name"], info["freq"], aos_dt.strftime("%H:%M:%S"), f"{max_el:.1f}°", los_dt.strftime("%H:%M:%S"), cd))
        except Exception:
            continue
    passes.sort(key=lambda x: x)
    return passes[:8]

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path)
        lat, lon, alt, min_el, rx_dirs, sdr_summary, sats = load_station()
        if path.startswith("/img/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts.isdigit():
                dir_idx, fname = int(parts), os.path.basename(parts)
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
        passes = get_upcoming_passes(lat, lon, alt, min_el, sats)
        pass_rows = "".join(f"<tr class={'live' if cd=='LIVE' else ''}><td><strong>{html.escape(name)}</strong></td><td>{freq/1e6:.3f} MHz</td><td>{aos}</td><td>{mel}</td><td>{los}</td><td><span class=pill>{cd}</span></td></tr>" for _, name, freq, aos, mel, los, cd in passes) or "<tr><td colspan=6>No passes above horizon in the next 24h (or TLEs updating).</td></tr>"
        logs = open(LOG_FILE, errors="ignore").read()[-3500:] if os.path.exists(LOG_FILE) else "Waiting for sat_tracker.log..."
        logs = html.escape("\n".join(logs.splitlines()[-20:]))
        all_imgs = []
        for idx, d in enumerate(rx_dirs):
            for p in glob.glob(os.path.join(d, "*.*")):
                if p.lower().endswith((".png", ".jpg", ".jpeg")):
                    all_imgs.append((os.path.getmtime(p), idx, p))
        all_imgs.sort(reverse=True)
        cards = [f'<div class="card"><a href="/img/{idx}/{os.path.basename(p)}" target="_blank"><img src="/img/{idx}/{os.path.basename(p)}" loading="lazy"></a><div class="meta"><strong>{html.escape(os.path.basename(p))}</strong><br><span>{datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")} (SDR #{idx})</span></div></div>' for mtime, idx, p in all_imgs]
        gallery = "".join(cards) if cards else '<p class="empty">No SSTV frames decoded yet — station is armed and waiting for the next pass.</p>'
        badges = "".join(f'<span class="badge">{html.escape(s)}</span>' for s in sdr_summary)
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><title>Auto-SSTV Station</title><style>body{{font-family:system-ui,sans-serif;background:#0f172a;color:#f8fafc;margin:0;padding:24px}}.wrap{{max-width:1180px;margin:0 auto}}h1{{margin:0 0 8px;font-size:1.6rem}}h2{{margin:24px 0 12px;font-size:1.15rem;color:#93c5fd}}.badges{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}}.badge{{background:#1e293b;border:1px solid #334155;padding:4px 10px;border-radius:999px;font-size:.85rem;color:#38bdf8}}table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden;font-size:.9rem}}th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid #334155}}th{{background:#020617;color:#93c5fd}}tr.live{{background:#064e3b}}.pill{{background:#0f172a;border:1px solid #38bdf8;color:#38bdf8;padding:2px 8px;border-radius:999px;font-size:.8rem;font-weight:600}}pre{{background:#1e293b;border:1px solid #334155;padding:14px;border-radius:10px;overflow-x:auto;font-size:.85rem;line-height:1.45;color:#e2e8f0}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}}.card{{background:#1e293b;border:1px solid #334155;border-radius:10px;overflow:hidden}}.card img{{width:100%;display:block;aspect-ratio:4/3;object-fit:cover;background:#020617}}.meta{{padding:10px 12px;font-size:.85rem}}.meta span{{color:#94a3b8;font-size:.78rem}}.empty{{background:#1e293b;padding:20px;border-radius:10px;color:#94a3b8}}</style></head><body><div class="wrap"><h1>Auto-SSTV Station — Live Dashboard</h1><div class="badges">{badges}<span class="badge">Min Elev: {min_el}°</span><span class="badge">Auto-Refresh: 15s</span></div><h2>Tracked Satellites ({len(sats)})</h2><div class="badges">{"".join(f'<span class="badge">{html.escape(v["name"])} ({v["freq"]/1e6:.3f} MHz)</span>' for v in sats.values())}</div><h2>Upcoming Overhead Passes (Next 24h)</h2><table><thead><tr><th>Satellite</th><th>Downlink</th><th>AOS (Local)</th><th>Max Elev</th><th>LOS (Local)</th><th>Countdown</th></tr></thead><tbody>{pass_rows}</tbody></table><h2>Live Tracker Log</h2><pre>{logs}</pre><h2>Decoded SSTV Gallery ({len(all_imgs)})</h2><div class="grid">{gallery}</div></div></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())
    def log_message(self, *args): pass

if __name__ == "__main__":
    print("Auto-SSTV Web Dashboard running on http://0.0.0")
    HTTPServer(("0.0.0.0", 8080), DashboardHandler).serve_forever()
