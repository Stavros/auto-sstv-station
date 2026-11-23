#!/bin/bash

sleep 10

if ! pactl list short sinks | grep -q "sstv_sink"; then
    pactl load-module module-null-sink sink_name=sstv_sink sink_properties=device.description="SSTV_Virtual_Cable"
fi
pactl set-default-sink sstv_sink
pactl set-default-source sstv_sink.monitor

sed -i 's/crashed=true/crashed=false/g' "$HOME/.config/gqrx/default.conf"
gqrx &

for i in {1..30}; do
    RESP=$(python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",7356),1); s.sendall(b"U DSP 1\n"); print(s.recv(64).decode().strip())' 2>/dev/null)
    if [ "$RESP" = "RPRT 0" ]; then
        break
    fi
    sleep 1
done

qsstv &
sleep 3
gpredict &
sleep 2
python3 -u "$HOME/sat_tracker.py" > "$HOME/sat_tracker.log" 2>&1 &
python3 -u "$HOME/web_ui.py" > "$HOME/web_ui.log" 2>&1 &
