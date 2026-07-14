"""Spawns the real C++ orderbook_server binary for integration tests."""
import os
import socket
import subprocess
import time
import unittest

_ENGINE_BUILD = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "engine", "build"))
SERVER_BIN = os.path.join(_ENGINE_BUILD, "orderbook_server")


class ServerProcess:
    def __init__(self, port: int):
        if not os.path.exists(SERVER_BIN):
            raise unittest.SkipTest(f"{SERVER_BIN} not built - run `make server` in engine/ first")
        self.port = port
        self._proc = subprocess.Popen([SERVER_BIN, str(port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_until_listening()

    def _wait_until_listening(self):
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.01)
        raise RuntimeError("orderbook_server never started listening")

    def stop(self):
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
