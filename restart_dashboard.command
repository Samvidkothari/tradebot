#!/bin/bash
# Double-click to restart the tradebot dashboard.
cd "/Users/samvid/projects/tradebot" || exit 1
echo "Stopping any running dashboard on :5050 …"
pkill -f "dashboard.py" 2>/dev/null
# also free the port if something else holds it
lsof -ti tcp:5050 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1
echo "Starting dashboard → http://127.0.0.1:5050  (Ctrl+C to stop)"
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python dashboard.py
else
  exec python3 dashboard.py
fi
