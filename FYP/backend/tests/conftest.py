"""
Shared pytest fixtures for ANPR backend tests.
"""
import os
import sys
import types
import unittest.mock as mock

# Ensure backend root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set required env vars before any app import
os.environ.setdefault("DB_HOST",     "localhost")
os.environ.setdefault("DB_USER",     "anpr_user")
os.environ.setdefault("DB_PASSWORD", "anpr_pass123")
os.environ.setdefault("DB_NAME",     "anpr_db")
os.environ.setdefault("SECRET_KEY",  "test-secret-key-for-unit-tests-only-32chars!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "Anpr@Secure2024!")

# Stub camera worker so tests never touch real hardware
_fake_cw = types.ModuleType("camera_worker_optimized")
_fake_cw.start      = lambda *a, **kw: None
_fake_cw.stop       = lambda: None
_fake_cw.is_alive   = lambda: True
_fake_cw.register   = lambda ws: None
_fake_cw.unregister = lambda ws: None
_fake_cw.get_stats  = lambda: {}
sys.modules["camera_worker_optimized"] = _fake_cw
