"""app/__init__.py
Global application initialization.
Ensures reliable IPv4 networking on Windows systems where IPv6 routes are absent.
"""
import socket

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    # Force AF_INET (IPv4) to avoid Windows IPv6 connection timeouts
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo
