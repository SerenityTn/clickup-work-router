"""Webhook security helpers."""
from __future__ import annotations

import hmac
import hashlib


def compute_clickup_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_clickup_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
    if not secret or not signature:
        return False
    supplied = signature.strip()
    if supplied.startswith("sha256="):
        supplied = supplied.split("=", 1)[1]
    expected = compute_clickup_signature(body, secret)
    return hmac.compare_digest(expected, supplied)
