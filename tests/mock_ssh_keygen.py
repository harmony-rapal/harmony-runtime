"""
mock_ssh_keygen.py — Subprocess boundary mock for ssh-keygen -Y verify.

Provides faithful simulation of OpenSSH ssh-keygen -Y verify in test environments
(such as the Builder container) where /usr/bin/ssh-keygen is absent.

In production / host environments, actual /usr/bin/ssh-keygen is executed directly.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import os
import struct
import subprocess
from typing import Any

from telegraph.human_gate import (
    HUMAN_GATE_NAMESPACE,
    compute_tosign_buffer,
    dearmor_sshsig,
    parse_allowed_signers,
    parse_sshsig,
)


def _get_libcrypto() -> Any:
    lib_name = ctypes.util.find_library("crypto") or "libcrypto.so.3"
    return ctypes.CDLL(lib_name)


def _mock_verify_ed25519(pubkey_32: bytes, signature_64: bytes, tosign: bytes) -> bool:
    try:
        lib = _get_libcrypto()
        nid = lib.OBJ_sn2nid(b"ED25519")
        if nid == 0:
            return False

        lib.EVP_PKEY_new_raw_public_key.restype = ctypes.c_void_p
        lib.EVP_PKEY_new_raw_public_key.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.EVP_MD_CTX_new.restype = ctypes.c_void_p
        lib.EVP_MD_CTX_free.argtypes = [ctypes.c_void_p]
        lib.EVP_DigestVerifyInit.restype = ctypes.c_int
        lib.EVP_DigestVerifyInit.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.EVP_DigestVerify.restype = ctypes.c_int
        lib.EVP_DigestVerify.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.EVP_PKEY_free.argtypes = [ctypes.c_void_p]

        pkey = lib.EVP_PKEY_new_raw_public_key(nid, None, pubkey_32, 32)
        if not pkey:
            return False
        ctx = lib.EVP_MD_CTX_new()
        if not ctx:
            lib.EVP_PKEY_free(pkey)
            return False

        try:
            if lib.EVP_DigestVerifyInit(ctx, None, None, None, pkey) != 1:
                return False
            ret = lib.EVP_DigestVerify(ctx, signature_64, 64, tosign, len(tosign))
            return ret == 1
        finally:
            lib.EVP_MD_CTX_free(ctx)
            lib.EVP_PKEY_free(pkey)
    except Exception:
        return False


def mock_ssh_keygen_verify(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """
    Simulate ssh-keygen -Y verify -f <allowed_signers> -I <principal> -n <namespace> -s <sig_file>
    with stdin containing message bytes.
    """
    if kwargs.get("shell"):
        raise RuntimeError("shell=True is strictly forbidden for ssh-keygen execution")

    if len(cmd) < 3 or cmd[1] != "-Y" or cmd[2] != "verify":
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=b"ssh-keygen: invalid arguments for verify mode\n",
        )

    try:
        f_idx = cmd.index("-f") + 1
        allowed_signers_path = cmd[f_idx]
        I_idx = cmd.index("-I") + 1
        principal = cmd[I_idx]
        n_idx = cmd.index("-n") + 1
        namespace = cmd[n_idx]
        s_idx = cmd.index("-s") + 1
        sig_file_path = cmd[s_idx]
    except (ValueError, IndexError) as exc:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: missing required option ({exc})\n".encode(),
        )

    # 1. Namespace check
    if namespace != HUMAN_GATE_NAMESPACE:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: invalid namespace {namespace!r}, expected {HUMAN_GATE_NAMESPACE!r}\n".encode(),
        )

    # 2. Read allowed_signers file
    if not os.path.exists(allowed_signers_path):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: allowed_signers file {allowed_signers_path!r} not found\n".encode(),
        )

    try:
        with open(allowed_signers_path, "r", encoding="utf-8") as f:
            as_content = f.read()
        allowed_entries = parse_allowed_signers(as_content)
    except Exception as exc:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: failed to parse allowed_signers ({exc})\n".encode(),
        )

    # 3. Read and parse signature file
    if not os.path.exists(sig_file_path):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: signature file {sig_file_path!r} not found\n".encode(),
        )

    try:
        with open(sig_file_path, "r", encoding="utf-8") as f:
            sig_text = f.read()
        sig_blob = dearmor_sshsig(sig_text)
        parsed_sig = parse_sshsig(sig_blob)
    except Exception as exc:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: signature parsing error: {exc}\n".encode(),
        )

    # 4. Check namespace in signature wire bytes
    if parsed_sig.namespace != namespace:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"ssh-keygen: signature namespace {parsed_sig.namespace!r} does not match {namespace!r}\n".encode(),
        )

    # 5. Check allowed_signers match for principal and pubkey
    matching_entries = [
        e
        for e in allowed_entries
        if e.matches_principal(principal)
        and e.matches_namespace(namespace)
        and e.matches_key(parsed_sig.pubkey_raw)
    ]
    if not matching_entries:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=f"No principal matched for identity {principal!r}\n".encode(),
        )

    # 6. Verify signature over stdin input
    input_bytes = kwargs.get("input")
    if input_bytes is None:
        input_bytes = b""
    elif isinstance(input_bytes, str):
        input_bytes = input_bytes.encode("utf-8")

    tosign = compute_tosign_buffer(
        namespace=parsed_sig.namespace,
        hash_algorithm=parsed_sig.hash_algorithm,
        message=input_bytes,
    )
    verified = _mock_verify_ed25519(
        pubkey_32=parsed_sig.pubkey_raw,
        signature_64=parsed_sig.signature_raw,
        tosign=tosign,
    )
    if not verified:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=b"",
            stderr=b"Signature verification failed\n",
        )

    key_fp = hashlib.sha256(parsed_sig.pubkey_raw).hexdigest()
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=0,
        stdout=f'Good "{namespace}" signature for {principal} with ED25519 key SHA256:{key_fp}\n'.encode("utf-8"),
        stderr=b"",
    )


_installed = False
_orig_subprocess_run = subprocess.run


def install_ssh_keygen_mock() -> None:
    global _installed, _orig_subprocess_run
    if _installed:
        return

    def patched_run(args: Any, *pargs: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        if isinstance(args, (list, tuple)) and len(args) > 0 and args[0] == "/usr/bin/ssh-keygen":
            return mock_ssh_keygen_verify(args, *pargs, **kwargs)
        return _orig_subprocess_run(args, *pargs, **kwargs)

    subprocess.run = patched_run
    _installed = True
