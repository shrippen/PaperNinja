#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

# Start at $PORT (default 8080) and try the next ports if it is taken.
port="${PORT:-8080}"
last=$((port + 20))

port_free() {
    ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

while ! port_free "$port"; do
    echo "Port $port is in use, trying $((port + 1))..." >&2
    port=$((port + 1))
    if [ "$port" -gt "$last" ]; then
        echo "No free port found." >&2
        exit 1
    fi
done

exec .venv/bin/python -m uvicorn app.main:app --reload --port "$port" "$@"
