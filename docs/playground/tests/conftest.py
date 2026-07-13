import contextlib
import http.server
from pathlib import Path
import socket
import threading

import pytest

PLAYGROUND_DIR = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def _serve(directory: Path):
    def handler(*a, **kw):
        return http.server.SimpleHTTPRequestHandler(*a, directory=str(directory), **kw)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def playground_url():
    with _serve(PLAYGROUND_DIR) as url:
        yield url
