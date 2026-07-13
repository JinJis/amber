"""SC-0.2/CR-10: admin console hardening — login throttle + IP allowlist.

Reuses the app/client wiring from test_admin.py (env + DB reflection happen there at import).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from adminpanel.config import settings
from adminpanel.main import _login_throttle, app
from adminpanel.security import LoginThrottle, ip_allowed

client = TestClient(app)


# --- pure helpers ---------------------------------------------------------
def test_login_throttle_locks_and_expires():
    t = LoginThrottle(max_fails=3, window_s=100.0, lockout_s=100.0)
    clock = [1000.0]
    t._now = lambda: clock[0]  # type: ignore[method-assign]

    assert not t.locked("1.2.3.4")
    for _ in range(3):
        t.record_failure("1.2.3.4")
    assert t.locked("1.2.3.4")            # 3rd failure trips the lockout
    assert not t.locked("9.9.9.9")        # per-IP, other IPs unaffected

    clock[0] += 101.0                      # lockout window elapsed
    assert not t.locked("1.2.3.4")

    # a success clears the counter
    for _ in range(2):
        t.record_failure("5.5.5.5")
    t.clear("5.5.5.5")
    t.record_failure("5.5.5.5")
    assert not t.locked("5.5.5.5")


def test_ip_allowed():
    assert ip_allowed("1.2.3.4", "") is True           # empty allowlist → all
    assert ip_allowed("10.1.2.3", "10.0.0.0/8") is True
    assert ip_allowed("203.0.113.5", "203.0.113.5") is True
    assert ip_allowed("8.8.8.8", "10.0.0.0/8,192.168.0.0/16") is False
    assert ip_allowed("not-an-ip", "10.0.0.0/8") is False   # fail closed


# --- login flow -----------------------------------------------------------
def test_login_locks_out_after_repeated_failures():
    _login_throttle.clear("testclient")
    for _ in range(5):
        r = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert r.status_code in (401, 429)
    # further attempts (even with the right password) are throttled
    assert client.post("/login", data={"username": "admin", "password": "wrong"}).status_code == 429
    r = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert r.status_code == 429
    _login_throttle.clear("testclient")  # don't leak the lockout to other tests


def test_ip_allowlist_gates_console(monkeypatch):
    _login_throttle.clear("testclient")
    monkeypatch.setattr(settings, "adminui_ip_allowlist", "203.0.113.0/24")
    # testclient's synthetic host is not in the allowlist → refused before the login form
    assert client.get("/login", follow_redirects=False).status_code == 403
    # healthz stays open (container healthcheck)
    assert client.get("/healthz").status_code == 200
    # an allowed forwarded IP passes the gate (reaches the login form)
    r = client.get("/login", headers={"X-Forwarded-For": "203.0.113.9"}, follow_redirects=False)
    assert r.status_code == 200
    monkeypatch.setattr(settings, "adminui_ip_allowlist", "")
