#!/usr/bin/env python3
import os
import glob
import json
import time
import socket
import subprocess
import urllib.request
import ephem

# ==========================================
# DEFAULT CONFIGURATION (Override in ~/config.py)
# ==========================================
LAT = None             # None = Auto-detect via Gpredict .qth or IP geolocation
LON = None
ALT = 0
MIN_ELEVATION = 5.0

# Empty dict {} = Core 3 sats + auto-discover active 2m SSTV sats via SatNOGS DB
SATS = {}

RX_DIR = os.path.expanduser('~/qsstv/rx_sstv')
DISCORD_WEBHOOK = ""

# Load private/local overrides from ~/config.py (ignored by Git)
try:
    from config import *  # noqa: F401, F403
except ImportError:
    pass

CORE_SATS = {
    '25544': ('ISS', 145800000),
    '59112': ('SONATE-2', 145880000),
    '57172': ('UMKA-1', 145825000),
}

PARK_FREQ = 145800000
GQRX_HOST = '127.0.0.1'
GQRX_PORT = 7356
SPEED_OF_LIGHT = 299792458.0
# ==========================================


def resolve_coordinates():
    if LAT and LON:
        print(f"[CONFIG] Using static coordinates: LAT={LAT}, LON={LON}, ALT={ALT}m", flush=True)
        return str(LAT), str(LON), float(ALT or 0)

    # Try Gpredict default ground station (.qth) first
    for qth in glob.glob(os.path.expanduser('~/.config/Gpredict/*.qth')):
        try:
            q_lat = q_lon = None
            q_alt = 0.0
            with open(qth, 'r') as f:
                for line in f:
                    if line.startswith('LAT='):
                        q_lat = line.split('=', 1)[1].strip()
                    elif line.startswith('LON='):
                        q_lon = line.split('=', 1)[1].strip()
                    elif line.startswith('ALT='):
                        q_alt = float(line.split('=', 1)[1].strip())
            if q_lat and q_lon:
                print(f"[AUTO] Loaded coordinates from Gpredict QTH: LAT={q_lat}, LON={q_lon}", flush=True)
                return q_lat, q_lon, q_alt
        except Exception:
            pass

    # Fallback to IP geolocation
    try:
        req = urllib.request.urlopen('http://ip-api.com', timeout=5)
        data = json.loads(req.read().decode('utf-8'))
        if data.get('status') == 'success':
            a_lat, a_lon = str(data['lat']), str(data['lon'])
            print(f"[AUTO] Auto-detected coordinates via IP ({data.get('city', 'Unknown')}): LAT={a_lat}, LON={a_lon}", flush=True)
            return a_lat, a_lon, float(ALT or 0)
    except Exception as e:
        print(f"[WARN] IP geolocation failed ({e}), defaulting to 0.00, 0.00", flush=True)

    return '0.00', '0.00', 0.0


def discover_2m_sstv_sats():
    if SATS:
        print(f"[CONFIG] Using static satellite list ({len(SATS)} configured).", flush=True)
        return SATS

    # Always keep the 3 core 2m SSTV candidates as a guaranteed baseline
    merged = dict(CORE_SATS)
    try:
        url = "https://db.satnogs.org/api/transmitters/?format=json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (SatNOGS-Client)', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode('utf-8').strip()
            data = json.loads(raw) if raw else []
            tx_list = data.get('results', data) if isinstance(data, dict) else data
        added = 0
        for tx in tx_list:
            if not tx.get('alive') or tx.get('status') != 'active':
                continue
            freq = tx.get('downlink_low')
            norad = str(tx.get('norad_cat_id') or '')
            mode_str = str(tx.get('mode') or '').upper()
            desc_str = str(tx.get('description') or '').upper()
            if norad and freq and 144000000 <= freq <= 146000000 and ('SSTV' in mode_str or 'SSTV' in desc_str) and norad not in merged:
                name = tx.get('description') or f"NORAD-{norad}"
                merged[norad] = (name, int(freq))
                added += 1
        print(f"[AUTO] Tracking {len(merged)} satellites ({len(CORE_SATS)} core + {added} discovered via SatNOGS DB).", flush=True)
    except Exception as e:
        print(f"[WARN] SatNOGS auto-discovery unavailable ({e}), using {len(merged)} core satellites.", flush=True)

    return merged


def fetch_celestrak_tle(norad):
    try:
        url = f"https://celestrak.org{norad}&FORMAT=TLE"
        with urllib.request.urlopen(url, timeout=8) as resp:
            lines = [ln.strip() for ln in resp.read().decode('utf-8').splitlines() if ln.strip()]
        if len(lines) >= 3 and lines[1].startswith('1 ') and lines[2].startswith('2 '):
            return lines[0], lines[1], lines[2]
        if len(lines) == 2 and lines[0].startswith('1 ') and lines[1].startswith('2 '):
            return f"NORAD-{norad}", lines[0], lines[1]
    except Exception:
        pass
    return None


def load_tles(sat_catalog):
    sats = {}
    sat_dir = os.path.expanduser('~/.config/Gpredict/satdata')
    for norad, (name, freq) in sat_catalog.items():
        tle_file = os.path.join(sat_dir, f"{norad}.sat")
        l1 = l2 = None
        if os.path.exists(tle_file):
            with open(tle_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('TLE1='):
                        l1 = line.split('=', 1)[1].strip()
                    elif line.startswith('TLE2='):
                        l2 = line.split('=', 1)[1].strip()
        if not (l1 and l2):
            fetched = fetch_celestrak_tle(norad)
            if fetched:
                _, l1, l2 = fetched
        if l1 and l2:
            sats[norad] = (name, freq, ephem.readtle(name, l1, l2))
    return sats


def send_gqrx(cmd):
    try:
        with socket.create_connection((GQRX_HOST, GQRX_PORT), timeout=1.0) as s:
            s.sendall(f"{cmd}\n".encode('ascii'))
            return s.recv(64).decode('ascii', errors='ignore').strip()
    except Exception:
        return None


def get_rx_files():
    if not os.path.isdir(RX_DIR):
        return set()
    files = set()
    for ext in ('*.png', '*.jpg', '*.jpeg'):
        files.update(glob.glob(os.path.join(RX_DIR, ext)))
    return files


def send_discord(filepath, sat_name, elev_deg, freq_hz):
    if not DISCORD_WEBHOOK:
        return
    try:
        time.sleep(1.5)
        target_label = f"**{sat_name}** ({elev_deg:.1f}° elev)" if sat_name else "2m VHF Band"
        msg = f"🛰️ **New SSTV Image Decoded!**\n• **Source:** {target_label}\n• **Freq:** `{freq_hz / 1e6:.5f} MHz`\n• **File:** `{os.path.basename(filepath)}`"
        subprocess.run([
            'curl', '-s', '-F', f'content={msg}',
            '-F', f'file=@{filepath}',
            DISCORD_WEBHOOK
        ], check=False, timeout=15)
        print(f"[DISCORD] Sent {os.path.basename(filepath)}", flush=True)
    except Exception as e:
        print(f"[DISCORD] Error: {e}", flush=True)


def main():
    lat, lon, alt = resolve_coordinates()
    obs = ephem.Observer()
    obs.lat = lat
    obs.lon = lon
    obs.elevation = alt

    sat_catalog = discover_2m_sstv_sats()
    sats = load_tles(sat_catalog)
    last_reload = time.time()
    last_freq = PARK_FREQ
    current_target = None
    current_elev = 0.0
    known_files = get_rx_files()

    print(f"Active pool: {len(sats)} satellites. Watching {RX_DIR} ({len(known_files)} existing images).", flush=True)

    while True:
        if time.time() - last_reload > 21600:
            sat_catalog = discover_2m_sstv_sats()
            sats = load_tles(sat_catalog)
            last_reload = time.time()

        obs.date = ephem.now()
        best_sat = None
        best_alt = -90.0

        for norad, (name, base_freq, body) in sats.items():
            body.compute(obs)
            alt_deg = body.alt * 180.0 / ephem.pi
            if alt_deg >= MIN_ELEVATION and alt_deg > best_alt:
                best_alt = alt_deg
                doppler = -base_freq * (body.range_velocity / SPEED_OF_LIGHT)
                tuned_freq = int(round(base_freq + doppler))
                best_sat = (name, alt_deg, tuned_freq, int(round(doppler)))

        if best_sat:
            name, alt_deg, tuned_freq, doppler = best_sat
            current_elev = alt_deg
            if name != current_target:
                print(f"[AOS] Tracking {name} | Elev: {alt_deg:.1f}° | Freq: {tuned_freq} Hz", flush=True)
                current_target = name
            if abs(tuned_freq - last_freq) >= 10:
                send_gqrx(f"F {tuned_freq}")
                last_freq = tuned_freq
        else:
            if current_target is not None:
                print(f"[LOS] Pass ended. Parking on {PARK_FREQ} Hz.", flush=True)
                current_target = None
                current_elev = 0.0
            if last_freq != PARK_FREQ:
                send_gqrx(f"F {PARK_FREQ}")
                last_freq = PARK_FREQ

        current_files = get_rx_files()
        new_files = sorted(current_files - known_files)
        for fpath in new_files:
            send_discord(fpath, current_target, current_elev, last_freq)
        known_files = current_files

        time.sleep(1)


if __name__ == '__main__':
    main()
