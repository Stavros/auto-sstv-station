# Copy this file to ~/config.py to customize your station (optional).
# Leave values as None / {} to use 100% Automatic discovery!

# Ground Station Coordinates:
# Set static strings (e.g., LAT = '40.60', LON = '22.90') OR leave None for automatic detection
LAT = None
LON = None
ALT = 0
MIN_ELEVATION = 5.0

# Satellite List:
# Leave {} to track the 3 core 2m SSTV satellites + auto-discover new ones via SatNOGS DB,
# OR provide a static dictionary to track ONLY your custom list: {'25544': ('ISS', 145800000)}
SATS = {}

# QSSTV received images directory & optional Discord Webhook URL
RX_DIR = '/home/pi/qsstv/rx_sstv'
DISCORD_WEBHOOK = ""
