"""Makes `orderbook_client` importable regardless of the caller's cwd."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
