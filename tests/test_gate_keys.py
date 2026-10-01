"""
test_gate_keys.py — Deterministic Ed25519 test keys for Harmony test suite.

Note: These keys are for TESTING ONLY and reside strictly in tests/,
preserving the invariant: NO private signing key inside Harmony runtime (telegraph/).
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import struct
from typing import Any

from telegraph.human_gate import set_test_key_resolver

# Deterministic 32-byte Ed25519 seeds for test actors
TEST_SEEDS = {
    "captain": bytes.fromhex("c1ad940f29c85acc0e84cb11856fbf52e70b4432a49f8fe2209822b7b592551b"),
    "captain@harmony.local": bytes.fromhex("c1ad940f29c85acc0e84cb11856fbf52e70b4432a49f8fe2209822b7b592551b"),
    "alice@corp.com": bytes.fromhex("df80d323733859c3f638c0eb3942d3566650d9f0f217904b428c2fe5a22542bf"),
    "bob@corp.com": bytes.fromhex("ac50fe2402e23005cd229dba54c361feb980fdabc5249c5dfa86087ec09c189d"),
    "charlie@corp.com": bytes.fromhex("d3d0d2ee23a6bc28e9762116e5cec678547f85ceb6d4b0135197ef08ba2a5589"),
    "eve@corp.com": bytes.fromhex("9d26021930ab49c9a9127802516f7cc6af2fb473eb4462153e1fcdbb5111157a"),
}

TEST_PUBKEYS_B64 = {
    "captain": "AAAAC3NzaC1lZDI1NTE5AAAAIPZA7VP1sbrqECLtf385Lc6o9jJtuso9tak67dJbKGzR",
    "captain@harmony.local": "AAAAC3NzaC1lZDI1NTE5AAAAIPZA7VP1sbrqECLtf385Lc6o9jJtuso9tak67dJbKGzR",
    "alice@corp.com": "AAAAC3NzaC1lZDI1NTE5AAAAIH+GxcvVerP/o0YsZob3wy8FCPFtTmeM27pNUtr0x7qZ",
    "bob@corp.com": "AAAAC3NzaC1lZDI1NTE5AAAAIBsQrplBeey4vW8uMnKpMoKLh4PVIQIj7/qz3s/9xHqA",
    "charlie@corp.com": "AAAAC3NzaC1lZDI1NTE5AAAAIFBCfL0g3IWc5CDbh0q1GdLVk+Wu3C9xgIX5GWyrcMnI",
    "eve@corp.com": "AAAAC3NzaC1lZDI1NTE5AAAAIKqAHb5gltMR14t6skzN3it5eECEEkhtyNsuDxPg4ikU",
}


def get_test_key(signer: str) -> bytes | None:
    if signer in ("eve@corp.com", "attacker", "forged"):
        return TEST_SEEDS["eve@corp.com"]
    if signer in TEST_SEEDS:
        return TEST_SEEDS[signer]
    # For any other test signer in tests, derive seed deterministically:
    return hashlib.sha256(f"harmony-test-key-seed-{signer}-2026".encode("utf-8")).digest()


def ensure_test_keys_registered() -> None:
    set_test_key_resolver(get_test_key)
    from tests.mock_ssh_keygen import install_ssh_keygen_mock
    install_ssh_keygen_mock()


def get_test_allowed_signers_content() -> str:
    return f"""# Test allowed signers
captain,captain@harmony.local namespaces="harmony-human-gate" ssh-ed25519 {TEST_PUBKEYS_B64['captain']} Captain Authority Key
alice@corp.com namespaces="harmony-human-gate" ssh-ed25519 {TEST_PUBKEYS_B64['alice@corp.com']} Alice Human Approver Key
bob@corp.com namespaces="harmony-human-gate" ssh-ed25519 {TEST_PUBKEYS_B64['bob@corp.com']} Bob Human Approver Key
charlie@corp.com namespaces="harmony-human-gate" ssh-ed25519 {TEST_PUBKEYS_B64['charlie@corp.com']} Charlie Human Approver Key
"""


# Register on import when running under test framework
ensure_test_keys_registered()
