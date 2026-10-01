"""
actuatord.py — Standalone daemon entry point for the Actuator Authority Boundary.

Usage:
  python3 -m telegraph.actuatord \
    --state-dir <path> \
    --socket-path <path> \
    --allowed-client-uid <uid> \
    --expected-socket-gid <gid>
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from typing import Any

from telegraph.actuator_service import (
    ActuatorServer,
    AuthorityConfigurationError,
    open_actuator_ledger,
    verify_authority_environment,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="actuatord",
        description="Harmony Telegraph Actuator — Authority Boundary Daemon",
    )
    parser.add_argument(
        "--state-dir",
        required=True,
        help="Path to exclusive Ledger state directory (mode 0700)",
    )
    parser.add_argument(
        "--socket-path",
        required=True,
        help="Path to Unix domain socket for IPC",
    )
    parser.add_argument(
        "--allowed-client-uid",
        type=int,
        required=True,
        help="Mandatory UID of the authorized ordinary caller",
    )
    parser.add_argument(
        "--expected-socket-gid",
        type=int,
        required=True,
        help="Mandatory GID of the harmony-telegraph group",
    )
    parser.add_argument(
        "--runner-socket-path",
        default=None,
        help="Optional path to Runner Unix domain socket",
    )
    parser.add_argument(
        "--gate-receipt-dir",
        default=None,
        help="Optional path to directory containing human gate receipts",
    )
    parser.add_argument(
        "--evidence-dir",
        default=None,
        help="Optional path to directory for builder evidence",
    )
    parser.add_argument(
        "--allowed-signers-path",
        default=None,
        help="Optional path to OpenSSH allowed_signers file",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    # 1. Establish restrictive umask before any Ledger/SQLite initialization
    os.umask(0o077)

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        # 2. Open / create the canonical Ledger under established authority umask
        ledger = open_actuator_ledger(args.state_dir)

        # 3. Create server and verify authority environment
        server = ActuatorServer(
            ledger=ledger,
            socket_path=args.socket_path,
            allowed_client_uid=args.allowed_client_uid,
            expected_socket_gid=args.expected_socket_gid,
            runner_socket_path=args.runner_socket_path,
            gate_receipt_dir=args.gate_receipt_dir,
            evidence_store_dir=args.evidence_dir,
            allowed_signers_path=args.allowed_signers_path,
        )
    except AuthorityConfigurationError as exc:
        print(f"ERROR=AUTHORITY_CHECK_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


    def _shutdown(signum: int, frame: Any) -> None:
        server.close()
        ledger.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    server.start()
    print(f"ACTUATORD_STARTED=YES SOCKET_PATH={args.socket_path}")
    try:
        while True:
            server.handle_one_connection()
    finally:
        server.close()
        ledger.close()


if __name__ == "__main__":
    main()
