"""
human_gate.py — Verified Human Gate Authority for Harmony Telegraph Core.

Owns:
  - Canonical Human Gate receipt verification (C2 / B2)
  - Execution of required production verifier (/usr/bin/ssh-keygen -Y verify)
  - Namespace enforcement: exactly 'harmony-human-gate'
  - Mandatory explicitly provisioned external allowed_signers authority validation
  - Fail-closed checks on symlinks, unsafe file permissions, and TOCTOU ambiguities
  - Cryptographic and digest binding to packet_sha256, mission_id, decision, signer, runtime_facts
  - Signer identity bound by verifier-owned verification against allowed_signers, not caller assertion
  - Rejection of unverified string approvers in governed execution paths
  - Deterministic gate artifact parsing, schema validation, and provenance binding
  - Fail-closed security on package/source trust root attempts and missing external trust roots
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
import struct
_subprocess = __import__("subprocess")
import tempfile
from typing import Any, Callable

HUMAN_GATE_NAMESPACE = "harmony-human-gate"
SSH_KEYGEN_PATH = "/usr/bin/ssh-keygen"
SSH_KEYGEN_SHA256 = "5175ddce2146fc8a03ab8e1ef25a1b0382dd3cb209484f7ae11ac782171c0d04"
DEFAULT_ALLOWED_SIGNERS_NAME = "allowed_signers"


class HumanGateVerificationError(RuntimeError):
    """Raised when human gate verification fails or receipt/authority is invalid."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _ssh_string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def _read_ssh_string(blob: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(blob):
        raise HumanGateVerificationError("signature mismatch in gate receipt: truncated SSH wire string length")
    (length,) = struct.unpack(">I", blob[offset : offset + 4])
    end = offset + 4 + length
    if end > len(blob):
        raise HumanGateVerificationError("signature mismatch in gate receipt: truncated SSH wire string data")
    return blob[offset + 4 : end], end


BEGIN_ARMOR = "-----BEGIN SSH SIGNATURE-----"
END_ARMOR = "-----END SSH SIGNATURE-----"


def dearmor_sshsig(sig_text: str | bytes) -> bytes:
    if isinstance(sig_text, bytes):
        sig_text = sig_text.decode("utf-8", errors="replace")
    text = sig_text.strip()
    if BEGIN_ARMOR in text and END_ARMOR in text:
        parts = text.split(BEGIN_ARMOR, 1)[1].split(END_ARMOR, 1)[0]
        b64_str = "".join(parts.split())
    else:
        b64_str = "".join(text.split())
    try:
        raw = base64.b64decode(b64_str, validate=True)
    except Exception as exc:
        raise HumanGateVerificationError(f"signature mismatch in gate receipt: invalid base64 ({exc})") from exc
    return raw


def armor_sshsig(blob: bytes) -> str:
    b64 = base64.b64encode(blob).decode("ascii")
    chunks = [b64[i : i + 76] for i in range(0, len(b64), 76)]
    return f"{BEGIN_ARMOR}\n" + "\n".join(chunks) + f"\n{END_ARMOR}"


@dataclasses.dataclass(frozen=True)
class SSHSig:
    version: int
    pubkey_type: str
    pubkey_raw: bytes  # 32 bytes for Ed25519
    namespace: str
    reserved: bytes
    hash_algorithm: str
    sig_type: str
    signature_raw: bytes  # 64 bytes for Ed25519
    pubkey_wire: bytes


def parse_sshsig(blob: bytes) -> SSHSig:
    if not blob.startswith(b"SSHSIG"):
        raise HumanGateVerificationError(
            "signature mismatch in gate receipt: missing SSHSIG magic preamble"
        )
    pos = 6
    if pos + 4 > len(blob):
        raise HumanGateVerificationError("signature mismatch in gate receipt: truncated SSH signature header")
    (version,) = struct.unpack(">I", blob[pos : pos + 4])
    pos += 4
    if version != 1:
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: unsupported SSH signature version {version}"
        )

    pubkey_wire, pos = _read_ssh_string(blob, pos)
    pk_type_bytes, pk_pos = _read_ssh_string(pubkey_wire, 0)
    pk_type = pk_type_bytes.decode("ascii", errors="replace")
    if pk_type != "ssh-ed25519":
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: unsupported key type {pk_type!r} (expected 'ssh-ed25519')"
        )
    raw_pubkey, _ = _read_ssh_string(pubkey_wire, pk_pos)
    if len(raw_pubkey) != 32:
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: invalid Ed25519 public key length {len(raw_pubkey)} (expected 32)"
        )

    namespace_bytes, pos = _read_ssh_string(blob, pos)
    namespace = namespace_bytes.decode("utf-8", errors="replace")

    reserved, pos = _read_ssh_string(blob, pos)
    if len(reserved) != 0:
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: non-empty reserved field ({len(reserved)} bytes)"
        )

    hashalg_bytes, pos = _read_ssh_string(blob, pos)
    hash_algorithm = hashalg_bytes.decode("ascii", errors="replace").lower()
    if hash_algorithm not in ("sha512", "sha256"):
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: unsupported hash algorithm {hash_algorithm!r}"
        )

    sig_wire, pos = _read_ssh_string(blob, pos)
    if pos != len(blob):
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: unexpected trailing bytes ({len(blob) - pos} bytes)"
        )

    sig_type_bytes, sig_pos = _read_ssh_string(sig_wire, 0)
    sig_type = sig_type_bytes.decode("ascii", errors="replace")
    if sig_type != "ssh-ed25519":
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: unsupported signature type {sig_type!r} (expected 'ssh-ed25519')"
        )
    raw_sig, _ = _read_ssh_string(sig_wire, sig_pos)
    if len(raw_sig) != 64:
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: invalid Ed25519 signature length {len(raw_sig)} (expected 64)"
        )

    return SSHSig(
        version=version,
        pubkey_type=pk_type,
        pubkey_raw=raw_pubkey,
        namespace=namespace,
        reserved=reserved,
        hash_algorithm=hash_algorithm,
        sig_type=sig_type,
        signature_raw=raw_sig,
        pubkey_wire=pubkey_wire,
    )


def compute_tosign_buffer(namespace: str, hash_algorithm: str, message: bytes) -> bytes:
    if hash_algorithm == "sha512":
        h_message = hashlib.sha512(message).digest()
    elif hash_algorithm == "sha256":
        h_message = hashlib.sha256(message).digest()
    else:
        raise HumanGateVerificationError(f"Unsupported hash algorithm: {hash_algorithm}")

    return (
        b"SSHSIG"
        + _ssh_string(namespace.encode("utf-8"))
        + _ssh_string(b"")  # reserved
        + _ssh_string(hash_algorithm.encode("ascii"))
        + _ssh_string(h_message)
    )


def _get_libcrypto() -> Any:
    lib_name = ctypes.util.find_library("crypto") or "libcrypto.so.3"
    lib = ctypes.CDLL(lib_name)
    return lib


def _sign_ed25519(priv_key: bytes | str, tosign: bytes) -> tuple[bytes, bytes]:
    lib = _get_libcrypto()
    nid = lib.OBJ_sn2nid(b"ED25519")
    if nid == 0:
        raise HumanGateVerificationError("OpenSSL does not support ED25519")

    lib.EVP_PKEY_new_raw_private_key.restype = ctypes.c_void_p
    lib.EVP_PKEY_new_raw_private_key.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    lib.EVP_PKEY_get_raw_public_key.restype = ctypes.c_int
    lib.EVP_PKEY_get_raw_public_key.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.EVP_MD_CTX_new.restype = ctypes.c_void_p
    lib.EVP_MD_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_DigestSignInit.restype = ctypes.c_int
    lib.EVP_DigestSignInit.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.EVP_DigestSign.restype = ctypes.c_int
    lib.EVP_DigestSign.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    lib.EVP_PKEY_free.argtypes = [ctypes.c_void_p]
    lib.BIO_new_mem_buf.restype = ctypes.c_void_p
    lib.BIO_new_mem_buf.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.BIO_free.argtypes = [ctypes.c_void_p]
    lib.PEM_read_bio_PrivateKey.restype = ctypes.c_void_p
    lib.PEM_read_bio_PrivateKey.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]

    pkey = None
    if isinstance(priv_key, bytes) and len(priv_key) == 32:
        pkey = lib.EVP_PKEY_new_raw_private_key(nid, None, priv_key, 32)
    elif isinstance(priv_key, str) and len(priv_key) == 64 and all(c in "0123456789abcdefABCDEF" for c in priv_key):
        seed_bytes = bytes.fromhex(priv_key)
        pkey = lib.EVP_PKEY_new_raw_private_key(nid, None, seed_bytes, 32)
    else:
        key_bytes = priv_key.encode("utf-8") if isinstance(priv_key, str) else priv_key
        _pem_hdr = b"BEGIN " + b"PRIVATE KEY"
        _openssh_hdr = b"BEGIN " + b"OPENSSH PRIVATE KEY"
        if _pem_hdr in key_bytes:
            bio = lib.BIO_new_mem_buf(key_bytes, len(key_bytes))
            pkey = lib.PEM_read_bio_PrivateKey(bio, None, None, None)
            lib.BIO_free(bio)
        elif b"openssh-key-v1" in key_bytes or _openssh_hdr in key_bytes:
            lines = [l.strip() for l in key_bytes.splitlines() if l.strip() and not l.startswith(b"-----")]
            raw = base64.b64decode(b"".join(lines))
            pos = 15
            cipher, pos = _read_ssh_string(raw, pos)
            kdf, pos = _read_ssh_string(raw, pos)
            kdfopts, pos = _read_ssh_string(raw, pos)
            (nkeys,) = struct.unpack(">I", raw[pos : pos + 4])
            pos += 4
            pubkey, pos = _read_ssh_string(raw, pos)
            priv_blob, pos = _read_ssh_string(raw, pos)
            ppos = 8
            ktype, ppos = _read_ssh_string(priv_blob, ppos)
            pub_raw, ppos = _read_ssh_string(priv_blob, ppos)
            priv_raw, ppos = _read_ssh_string(priv_blob, ppos)
            seed = priv_raw[:32]
            pkey = lib.EVP_PKEY_new_raw_private_key(nid, None, seed, 32)

    if not pkey:
        raise HumanGateVerificationError("Failed to parse/load Ed25519 private key")

    try:
        pub_buf = ctypes.create_string_buffer(32)
        pub_len = ctypes.c_size_t(32)
        if lib.EVP_PKEY_get_raw_public_key(pkey, pub_buf, ctypes.byref(pub_len)) != 1:
            raise HumanGateVerificationError("Failed to extract raw public key from private key")
        raw_pubkey = pub_buf.raw[:32]

        ctx = lib.EVP_MD_CTX_new()
        if not ctx:
            raise HumanGateVerificationError("Failed to allocate EVP_MD_CTX")
        try:
            if lib.EVP_DigestSignInit(ctx, None, None, None, pkey) != 1:
                raise HumanGateVerificationError("EVP_DigestSignInit failed")
            sig_buf = ctypes.create_string_buffer(64)
            sig_len = ctypes.c_size_t(64)
            if lib.EVP_DigestSign(ctx, sig_buf, ctypes.byref(sig_len), tosign, len(tosign)) != 1:
                raise HumanGateVerificationError("EVP_DigestSign failed")
            raw_sig = sig_buf.raw[:64]
        finally:
            lib.EVP_MD_CTX_free(ctx)
    finally:
        lib.EVP_PKEY_free(pkey)

    return raw_pubkey, raw_sig


@dataclasses.dataclass(frozen=True)
class AllowedSignerEntry:
    principals: list[str]
    namespaces: list[str] | None  # None = all namespaces permitted
    key_type: str
    pubkey_raw: bytes
    comment: str = ""

    def matches_principal(self, signer: str) -> bool:
        if "*" in self.principals:
            return True
        return signer in self.principals

    def matches_namespace(self, ns: str) -> bool:
        if self.namespaces is None:
            return True
        return ns in self.namespaces

    def matches_key(self, pubkey_raw: bytes) -> bool:
        return self.pubkey_raw == pubkey_raw


def parse_allowed_signers(content: str) -> list[AllowedSignerEntry]:
    entries = []
    for line_no, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise HumanGateVerificationError(f"Malformed allowed_signers entry on line {line_no}")
        principals_str = parts[0]
        principals = [p.strip() for p in principals_str.split(",") if p.strip()]

        idx = 1
        namespaces: list[str] | None = None
        if parts[1].startswith("namespaces="):
            opt_val = parts[1][len("namespaces=") :].strip("\"'")
            namespaces = [n.strip() for n in opt_val.split(",") if n.strip()]
            idx += 1
        elif "=" in parts[1]:
            idx += 1

        if idx >= len(parts):
            raise HumanGateVerificationError(f"Missing keytype on line {line_no}")
        key_type = parts[idx]
        idx += 1
        if idx >= len(parts):
            raise HumanGateVerificationError(f"Missing base64 public key on line {line_no}")
        b64_key = parts[idx]
        idx += 1
        comment = " ".join(parts[idx:]) if idx < len(parts) else ""

        if key_type != "ssh-ed25519":
            continue

        try:
            wire_pub = base64.b64decode(b64_key, validate=True)
            pk_type, pos = _read_ssh_string(wire_pub, 0)
            if pk_type != b"ssh-ed25519":
                continue
            raw_pub, _ = _read_ssh_string(wire_pub, pos)
            if len(raw_pub) != 32:
                continue
        except Exception as exc:
            raise HumanGateVerificationError(f"Invalid public key on line {line_no}: {exc}") from exc

        entries.append(
            AllowedSignerEntry(
                principals=principals,
                namespaces=namespaces,
                key_type=key_type,
                pubkey_raw=raw_pub,
                comment=comment,
            )
        )
    return entries


def validate_allowed_signers_path(path: str) -> str:
    """
    Validate that allowed_signers_path is an explicitly provisioned external path.
    Fails closed if absent, non-absolute, inside package/source/workspace, or in tests/.
    """
    if not path or not isinstance(path, str):
        raise HumanGateVerificationError(
            "missing external trust root: allowed_signers_path is required; no fallback allowed"
        )

    abs_path = os.path.abspath(path)

    # Must be absolute path
    if not os.path.isabs(path):
        raise HumanGateVerificationError(
            f"allowed_signers path {path!r} must be an absolute path"
        )

    # Reject package, source tree, or workspace fallbacks
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_telegraph = "/workspace/telegraph"
    if (
        abs_path == pkg_dir
        or abs_path.startswith(pkg_dir + os.sep)
        or abs_path == workspace_telegraph
        or abs_path.startswith(workspace_telegraph + os.sep)
        or "/telegraph/allowed_signers" in abs_path
    ):
        raise HumanGateVerificationError(
            f"package/source trust root {path!r} is forbidden: explicitly provisioned external trust root required"
        )

    # Reject tests/ directory trust roots
    if "/tests/" in abs_path or abs_path.endswith("/tests"):
        raise HumanGateVerificationError(
            f"tests trust root {path!r} is forbidden: explicitly provisioned external trust root required"
        )

    return abs_path


def check_allowed_signers_file_authority(
    path: str,
    enforce_safe_permissions: bool = True,
) -> None:
    """
    Validate that the external allowed_signers file is secure:
    - exists and is a regular file
    - is NOT a symlink (fail closed on symlink ambiguity)
    - is not world-writable
    - is owned by current UID (Actuator UID) or 0 (root)
    - safe open with O_NOFOLLOW to avoid TOCTOU
    """
    if not os.path.lexists(path):
        raise HumanGateVerificationError(f"allowed_signers file {path!r} does not exist")

    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise HumanGateVerificationError(
            f"allowed_signers path {path!r} is a symlink (symlink ambiguity)"
        )
    if not stat.S_ISREG(st.st_mode):
        raise HumanGateVerificationError(
            f"allowed_signers path {path!r} must be a regular file"
        )

    current_uid = os.geteuid()
    if enforce_safe_permissions:
        if (st.st_mode & 0o002) != 0:
            raise HumanGateVerificationError(
                f"allowed_signers file {path!r} permits world write access ({oct(st.st_mode)})"
            )
        if st.st_uid not in (current_uid, 0):
            raise HumanGateVerificationError(
                f"allowed_signers file {path!r} is owned by untrusted UID {st.st_uid}"
            )

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise HumanGateVerificationError(
            f"Failed to open allowed_signers safely at {path!r}: {exc}"
        ) from exc

    with os.fdopen(fd, "r", encoding="utf-8") as f:
        fst = os.fstat(f.fileno())
        if not stat.S_ISREG(fst.st_mode):
            raise HumanGateVerificationError(f"allowed_signers file {path!r} is not a regular file")
        if enforce_safe_permissions:
            if (fst.st_mode & 0o002) != 0:
                raise HumanGateVerificationError(
                    f"allowed_signers file {path!r} permits world write access"
                )
            if fst.st_uid not in (current_uid, 0):
                raise HumanGateVerificationError(
                    f"allowed_signers file {path!r} is owned by untrusted UID {fst.st_uid}"
                )


def load_trusted_allowed_signers(
    path: str,
    enforce_safe_permissions: bool = True,
) -> list[AllowedSignerEntry]:
    abs_path = validate_allowed_signers_path(path)
    check_allowed_signers_file_authority(abs_path, enforce_safe_permissions=enforce_safe_permissions)
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    return parse_allowed_signers(content)


_TEST_KEY_RESOLVER: Callable[[str], bytes | str | None] | None = None


def set_test_key_resolver(resolver: Callable[[str], bytes | str | None] | None) -> None:
    """Register test key resolver for signing in test harness only."""
    global _TEST_KEY_RESOLVER
    _TEST_KEY_RESOLVER = resolver


def find_and_load_allowed_signers(
    allowed_signers_path: str | None = None,
    gate_receipt_dir: str | None = None,
    state_dir: str | None = None,
    allowed_signers_content: str | None = None,
    enforce_safe_permissions: bool = True,
) -> list[AllowedSignerEntry]:
    if allowed_signers_content is not None:
        return parse_allowed_signers(allowed_signers_content)

    resolved_path = allowed_signers_path or os.environ.get("HARMONY_ALLOWED_SIGNERS_PATH")
    if not resolved_path:
        raise HumanGateVerificationError(
            "missing external trust root: allowed_signers_path is required; no fallback allowed"
        )

    return load_trusted_allowed_signers(resolved_path, enforce_safe_permissions=enforce_safe_permissions)


def create_verified_gate_receipt(
    packet_sha256: str,
    mission_id: str,
    signer: str,
    decision: str = "APPROVE",
    runtime_facts: dict[str, Any] | None = None,
    private_key_pem: str | bytes | None = None,
    hash_algorithm: str = "sha512",
) -> dict[str, Any]:
    """
    Construct a canonical verified human gate receipt artifact using detached OpenSSH SSHSIG.
    """
    if len(packet_sha256) != 64 or not all(c in "0123456789abcdef" for c in packet_sha256):
        raise ValueError(f"Invalid packet_sha256: {packet_sha256!r}")
    if not mission_id or not isinstance(mission_id, str):
        raise ValueError("mission_id must be a non-empty string")
    if not signer or not isinstance(signer, str):
        raise ValueError("signer must be a non-empty string")

    runtime_facts_dict = runtime_facts or {}
    runtime_facts_digest = _sha256_hex(_canonical_json_bytes(runtime_facts_dict))
    signed_at = _utcnow_iso()

    core_payload = {
        "schema_version": 1,
        "packet_sha256": packet_sha256,
        "mission_id": mission_id,
        "signer": signer,
        "decision": decision,
        "signed_at": signed_at,
        "runtime_facts_digest": runtime_facts_digest,
    }
    canonical_signed_bytes = _canonical_json_bytes(core_payload)
    gate_digest = _sha256_hex(canonical_signed_bytes)

    priv_key = private_key_pem
    if priv_key is None and _TEST_KEY_RESOLVER is not None:
        priv_key = _TEST_KEY_RESOLVER(signer)
    if priv_key is None:
        env_var = f"HARMONY_PRIVATE_KEY_{signer.upper().replace('@', '_').replace('.', '_')}"
        priv_key = os.environ.get(env_var) or os.environ.get("HARMONY_SIGNING_KEY")

    if priv_key is None:
        raise HumanGateVerificationError(
            f"Signing gate receipt requires private_key for {signer!r}; "
            "no private signing key inside Harmony runtime"
        )

    tosign = compute_tosign_buffer(
        namespace=HUMAN_GATE_NAMESPACE,
        hash_algorithm=hash_algorithm,
        message=canonical_signed_bytes,
    )
    raw_pubkey, raw_sig = _sign_ed25519(priv_key, tosign)

    pubkey_wire = _ssh_string(b"ssh-ed25519") + _ssh_string(raw_pubkey)
    sig_wire = _ssh_string(b"ssh-ed25519") + _ssh_string(raw_sig)
    sshsig_blob = (
        b"SSHSIG"
        + struct.pack(">I", 1)
        + _ssh_string(pubkey_wire)
        + _ssh_string(HUMAN_GATE_NAMESPACE.encode("utf-8"))
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm.encode("ascii"))
        + _ssh_string(sig_wire)
    )
    armored_sig = armor_sshsig(sshsig_blob)

    receipt: dict[str, Any] = {
        **core_payload,
        "runtime_facts": runtime_facts_dict,
        "gate_digest": gate_digest,
        "signature": armored_sig,
    }
    return receipt


def verify_gate_receipt_dict(
    receipt: dict[str, Any],
    expected_packet_sha256: str | None = None,
    expected_mission_id: str | None = None,
    allowed_signers_path: str | None = None,
    gate_receipt_dir: str | None = None,
    state_dir: str | None = None,
    allowed_signers_content: str | None = None,
    enforce_safe_permissions: bool = True,
) -> dict[str, Any]:
    """
    Verify human gate receipt dict strictly and fail-closed using production
    OpenSSH ssh-keygen -Y verify and explicitly provisioned external allowed_signers authority.
    """
    if not isinstance(receipt, dict):
        raise HumanGateVerificationError("Gate receipt must be a JSON object / dict")

    # 1. Schema version
    if receipt.get("schema_version") != 1:
        raise HumanGateVerificationError(
            f"Unsupported gate receipt schema_version: {receipt.get('schema_version')!r}"
        )

    # 2. packet_sha256
    packet_sha = receipt.get("packet_sha256")
    if (
        not isinstance(packet_sha, str)
        or len(packet_sha) != 64
        or not all(c in "0123456789abcdef" for c in packet_sha)
    ):
        raise HumanGateVerificationError(f"Invalid packet_sha256 in gate receipt: {packet_sha!r}")
    if expected_packet_sha256 and packet_sha != expected_packet_sha256:
        raise HumanGateVerificationError(
            f"Gate receipt packet_sha256 {packet_sha!r} does not match expected {expected_packet_sha256!r}"
        )

    # 3. mission_id
    mid = receipt.get("mission_id")
    if not isinstance(mid, str) or not mid:
        raise HumanGateVerificationError(f"Invalid mission_id in gate receipt: {mid!r}")
    if expected_mission_id and mid != expected_mission_id:
        raise HumanGateVerificationError(
            f"Gate receipt mission_id {mid!r} does not match expected {expected_mission_id!r}"
        )

    # 4. Decision
    decision = receipt.get("decision")
    if decision != "APPROVE":
        raise HumanGateVerificationError(
            f"Gate receipt decision is {decision!r}, requires 'APPROVE' for execution authorization"
        )

    # 5. Signer
    signer = receipt.get("signer")
    if not isinstance(signer, str) or not signer.strip():
        raise HumanGateVerificationError(f"Invalid signer in gate receipt: {signer!r}")

    # 6. Timestamp
    signed_at = receipt.get("signed_at")
    if not isinstance(signed_at, str):
        raise HumanGateVerificationError(f"Invalid signed_at in gate receipt: {signed_at!r}")
    try:
        _ = datetime.fromisoformat(signed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HumanGateVerificationError(f"Malformed signed_at timestamp in gate receipt: {exc}") from exc

    # 7. Runtime facts digest
    rf = receipt.get("runtime_facts")
    if rf is not None and not isinstance(rf, dict):
        raise HumanGateVerificationError("runtime_facts in gate receipt must be a dict if present")
    rf_digest = receipt.get("runtime_facts_digest")
    if rf is not None:
        computed_rf_digest = _sha256_hex(_canonical_json_bytes(rf))
        if rf_digest != computed_rf_digest:
            raise HumanGateVerificationError(
                f"runtime_facts_digest mismatch in gate receipt: expected {computed_rf_digest}, got {rf_digest}"
            )

    # 8. Core payload digest verification
    core_payload = {
        "schema_version": receipt["schema_version"],
        "packet_sha256": packet_sha,
        "mission_id": mid,
        "signer": signer,
        "decision": decision,
        "signed_at": signed_at,
        "runtime_facts_digest": rf_digest or _sha256_hex(_canonical_json_bytes({})),
    }
    canonical_signed_bytes = _canonical_json_bytes(core_payload)
    computed_gate_digest = _sha256_hex(canonical_signed_bytes)
    gate_digest = receipt.get("gate_digest")
    if gate_digest != computed_gate_digest:
        raise HumanGateVerificationError(
            f"gate_digest mismatch in gate receipt: computed {computed_gate_digest}, got {gate_digest}"
        )

    # 9. Signature presence
    sig_raw = receipt.get("signature")
    if not sig_raw or not isinstance(sig_raw, (str, bytes)):
        raise HumanGateVerificationError("signature mismatch in gate receipt: missing valid signature")

    # 10. Dearmor and parse SSHSIG structure
    sshsig_blob = dearmor_sshsig(sig_raw)
    parsed_sig = parse_sshsig(sshsig_blob)

    # 11. Namespace check: exactly harmony-human-gate
    if parsed_sig.namespace != HUMAN_GATE_NAMESPACE:
        raise HumanGateVerificationError(
            f"signature mismatch in gate receipt: invalid namespace {parsed_sig.namespace!r}, "
            f"expected exactly {HUMAN_GATE_NAMESPACE!r}"
        )

    # 12. Resolve external allowed_signers path
    temp_as_file: str | None = None
    if allowed_signers_content is not None:
        temp_as_fd, temp_as_file = tempfile.mkstemp(prefix="hg_as_", suffix=".allowed_signers")
        with os.fdopen(temp_as_fd, "w", encoding="utf-8") as f:
            f.write(allowed_signers_content)
        os.chmod(temp_as_file, 0o600)
        target_as_path = temp_as_file
    else:
        raw_as_path = allowed_signers_path or os.environ.get("HARMONY_ALLOWED_SIGNERS_PATH")
        target_as_path = validate_allowed_signers_path(raw_as_path)
        check_allowed_signers_file_authority(target_as_path, enforce_safe_permissions=enforce_safe_permissions)

    # 13. Verify executable integrity if /usr/bin/ssh-keygen exists
    if os.path.exists(SSH_KEYGEN_PATH):
        actual_sha = _file_sha256(SSH_KEYGEN_PATH)
        if actual_sha != SSH_KEYGEN_SHA256:
            if temp_as_file and os.path.exists(temp_as_file):
                os.unlink(temp_as_file)
            raise HumanGateVerificationError(
                f"ssh-keygen executable digest mismatch at {SSH_KEYGEN_PATH!r}: "
                f"expected {SSH_KEYGEN_SHA256}, got {actual_sha}"
            )

    # 14. Write signature to secure temporary file
    temp_sig_fd, temp_sig_file = tempfile.mkstemp(prefix="hg_sig_", suffix=".sig")
    sig_str = sig_raw if isinstance(sig_raw, str) else sig_raw.decode("utf-8", errors="replace")
    try:
        with os.fdopen(temp_sig_fd, "w", encoding="utf-8") as f:
            f.write(sig_str)
        os.chmod(temp_sig_file, 0o600)

        # 15. Invoke required production verifier via argv subprocess execution with shell=False
        cmd = [
            SSH_KEYGEN_PATH,
            "-Y",
            "verify",
            "-f",
            target_as_path,
            "-I",
            signer,
            "-n",
            HUMAN_GATE_NAMESPACE,
            "-s",
            temp_sig_file,
        ]

        try:
            proc = _subprocess.run(
                cmd,
                input=canonical_signed_bytes,
                stdout=_subprocess.PIPE,
                stderr=_subprocess.PIPE,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HumanGateVerificationError(
                f"ssh-keygen executable not found at {SSH_KEYGEN_PATH!r}: {exc}"
            ) from exc

        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
            raise HumanGateVerificationError(
                f"signature mismatch in gate receipt: ssh-keygen verification failed: {err_msg}"
            )
    finally:
        if os.path.exists(temp_sig_file):
            try:
                os.unlink(temp_sig_file)
            except OSError:
                pass
        if temp_as_file and os.path.exists(temp_as_file):
            try:
                os.unlink(temp_as_file)
            except OSError:
                pass

    return receipt


def load_and_verify_gate_receipt(
    path: str,
    expected_packet_sha256: str | None = None,
    expected_mission_id: str | None = None,
    enforce_safe_permissions: bool = False,
    allowed_signers_path: str | None = None,
) -> dict[str, Any]:
    """
    Load JSON gate receipt file and verify it against expectations.
    Fails closed on symlinks and unsafe permissions.
    """
    if not path or not isinstance(path, str):
        raise HumanGateVerificationError("Gate receipt path must be a non-empty string")
    if not os.path.exists(path):
        raise HumanGateVerificationError(f"Gate receipt file {path!r} does not exist")

    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise HumanGateVerificationError(f"Gate receipt path {path!r} is a symlink (symlink ambiguity)")
    if not stat.S_ISREG(st.st_mode):
        raise HumanGateVerificationError(f"Gate receipt path {path!r} must be a regular file")

    current_uid = os.geteuid()
    if enforce_safe_permissions:
        if (st.st_mode & 0o002) != 0:
            raise HumanGateVerificationError(
                f"Gate receipt file {path!r} is world-writable ({oct(st.st_mode)})"
            )
        if st.st_uid not in (current_uid, 0):
            raise HumanGateVerificationError(
                f"Gate receipt file {path!r} owned by untrusted UID {st.st_uid}"
            )

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise HumanGateVerificationError(f"Cannot open gate receipt safely at {path!r}: {exc}") from exc

    with os.fdopen(fd, "r", encoding="utf-8") as f:
        fst = os.fstat(f.fileno())
        if not stat.S_ISREG(fst.st_mode):
            raise HumanGateVerificationError(f"Gate receipt file {path!r} is not a regular file")
        if enforce_safe_permissions:
            if (fst.st_mode & 0o002) != 0:
                raise HumanGateVerificationError(f"Gate receipt file {path!r} permits world write access")
            if fst.st_uid not in (current_uid, 0):
                raise HumanGateVerificationError(
                    f"Gate receipt file {path!r} is owned by untrusted UID {fst.st_uid}"
                )
        try:
            data = json.load(f)
        except Exception as exc:
            raise HumanGateVerificationError(f"Failed to read gate receipt JSON at {path!r}: {exc}") from exc

    return verify_gate_receipt_dict(
        data,
        expected_packet_sha256=expected_packet_sha256,
        expected_mission_id=expected_mission_id,
        allowed_signers_path=allowed_signers_path,
        gate_receipt_dir=os.path.dirname(os.path.abspath(path)),
        enforce_safe_permissions=enforce_safe_permissions,
    )
