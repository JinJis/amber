"""SC-0.2 / CR-10: admin console hardening — login rate-limit + IP allowlist.

The admin panel holds CRUD over every service DB, so brute-forcing its single credential must be
throttled and (optionally) restricted to known source IPs. State is in-memory: the panel is a single
process (one uvicorn worker); if it is ever replicated this must move to a shared store.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field


@dataclass
class LoginThrottle:
    """Per-IP failed-login throttle. After ``max_fails`` failures inside ``window_s`` the IP is
    locked out for ``lockout_s``. A successful login clears the counter."""

    max_fails: int = 5
    window_s: float = 300.0
    lockout_s: float = 300.0
    _fails: dict[str, list[float]] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def _now(self) -> float:
        return time.monotonic()

    def locked(self, ip: str) -> bool:
        until = self._locked_until.get(ip)
        if until is None:
            return False
        if self._now() >= until:
            # lockout elapsed — forget it so the IP starts fresh
            self._locked_until.pop(ip, None)
            return False
        return True

    def record_failure(self, ip: str) -> None:
        now = self._now()
        recent = [t for t in self._fails.get(ip, []) if now - t < self.window_s]
        recent.append(now)
        self._fails[ip] = recent
        if len(recent) >= self.max_fails:
            self._locked_until[ip] = now + self.lockout_s
            self._fails[ip] = []

    def clear(self, ip: str) -> None:
        self._fails.pop(ip, None)
        self._locked_until.pop(ip, None)


def ip_allowed(ip: str, allowlist: str) -> bool:
    """True if ``ip`` falls inside any IP/CIDR in the comma-separated ``allowlist``.

    An empty allowlist means "allow all" (the default — so the local dev stack is unaffected). An
    unparseable client IP against a non-empty allowlist is denied (fail closed).
    """
    nets = [n.strip() for n in allowlist.split(",") if n.strip()]
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if "/" in n:
                if addr in ipaddress.ip_network(n, strict=False):
                    return True
            elif addr == ipaddress.ip_address(n):
                return True
        except ValueError:
            continue
    return False
