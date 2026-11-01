"""PS-59 (found measuring PS-57): `arcadedb_python`'s `SyncClient` sends every request with a bare `requests.post` /
`requests.get`, so each one opens and closes a TCP connection. At 8 concurrent writers against Docker Desktop on macOS
about two thirds of the writes failed with `RemoteDisconnected`; with keep-alive sessions none did. The store's client
keeps one connection per thread. Hermetic: a localhost HTTP/1.1 server that counts the connections it accepts."""
from __future__ import annotations

import concurrent.futures as cf
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from arcadedb_python import DatabaseDao, SyncClient

from rag_wright.store.arcadedb import ArcadeDBStore, _PooledClient


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, as ArcadeDB answers

    def setup(self):
        super().setup()
        with self.server.lock:
            self.server.connections += 1

    def _answer(self, result):
        body = json.dumps({"result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.server.lock:
            self.server.posts.append(payload)
        self._answer(["db"] if payload.get("command") == "list databases" else [])

    def do_GET(self):
        self._answer(True)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.lock, srv.connections, srv.posts = threading.Lock(), 0, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _client(cls, srv):
    return cls("127.0.0.1", str(srv.server_address[1]), username="u", password="p")


def test_the_library_client_opens_a_connection_per_request(server):
    """The premise: without pooling, every request is a new connection."""
    client = _client(SyncClient, server)
    for _ in range(10):
        client.post("api/v1/server", {"command": "list databases"})
    assert server.connections == 11  # the 10, plus the login check `SyncClient.__init__` sends


def test_one_thread_reuses_one_connection(server):
    client = _client(_PooledClient, server)
    for _ in range(10):
        client.post("api/v1/server", {"command": "list databases"})
        assert DatabaseDao.exists(client, "db")  # a GET
    assert server.connections == 1
    client.close()


def test_each_thread_keeps_its_own_connection(server):
    client = _client(_PooledClient, server)  # the login check runs on this thread: its connection is the first
    with cf.ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: [client.post("api/v1/server", {"command": f"c{i}-{j}"}) for j in range(25)],
                      range(4)))
    assert server.connections == 1 + 4
    assert len(server.posts) == 1 + 100  # every request answered
    client.close()


def test_close_releases_every_thread_session(server):
    client = _client(_PooledClient, server)
    with cf.ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda i: client.post("api/v1/server", {"command": "x"}), range(3)))
    sessions = list(client._sessions)
    assert len(sessions) >= 2  # the login thread's, and the workers'
    assert all(len(s.get_adapter("http://x").poolmanager.pools) == 1 for s in sessions)  # each held a connection
    client.close()
    assert not client._sessions
    assert all(len(s.get_adapter("http://x").poolmanager.pools) == 0 for s in sessions)  # and closed it


def test_the_store_is_built_on_the_pooled_client(server):
    store = ArcadeDBStore.from_config("127.0.0.1", str(server.server_address[1]), "u", "p", database="db")
    assert isinstance(store._client, _PooledClient)
    store.close()
    assert not store._client._sessions
