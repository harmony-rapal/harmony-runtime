"""
runnerd.py — Standalone daemon entry point for the Builder Runner Authority Boundary.

Usage:
  python3 -m telegraph.runnerd \
    --socket-path <path> \
    --allowed-actuator-uid <uid> \
    --expected-socket-gid <gid> \
    --agy-path <path> \
    --agy-sha256 <sha256> \
    [--helper-target <path>] \
    [--helper-sha256 <sha256>] \
    [--timeout-seconds <int>]
"""

from __future__ import annotations

import argparse
import signal
import sys
from typing import Any

from telegraph.runner_service import (
    RunnerAuthorityError,
    RunnerConfig,
    RunnerServer,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runnerd",
        description="Harmony Telegraph Runner — Distinct Builder Execution Authority Daemon",
    )
    parser.add_argument(
        "--socket-path",
        required=True,
        help="Path to Unix domain socket for Runner IPC",
    )
    parser.add_argument(
        "--allowed-actuator-uid",
        type=int,
        required=True,
        help="Mandatory UID of the authorized Actuator peer",
    )
    parser.add_argument(
        "--expected-socket-gid",
        type=int,
        required=True,
        help="Mandatory GID of the harmony-telegraph group",
    )
    parser.add_argument(
        "--agy-path",
        required=True,
        help="Canonical path to AGY executable",
    )
    parser.add_argument(
        "--agy-sha256",
        required=True,
        help="Expected SHA256 digest of AGY executable",
    )
    parser.add_argument(
        "--helper-target",
        default=None,
        help="Optional path to sealed agentapi helper target",
    )
    parser.add_argument(
        "--helper-sha256",
        default=None,
        help="Optional expected SHA256 digest of helper target",
    )
    parser.add_argument(
        "--profile-id",
        default="agy-builder-v1",
        help="Builder profile ID (default: agy-builder-v1)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=3600,
        help="Timeout in seconds for player execution (default: 3600)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = RunnerConfig(
        socket_path=args.socket_path,
        allowed_actuator_uid=args.allowed_actuator_uid,
        expected_socket_gid=args.expected_socket_gid,
        agy_path=args.agy_path,
        agy_sha256=args.agy_sha256,
        helper_target=args.helper_target,
        helper_sha256=args.helper_sha256,
        timeout_seconds=args.timeout_seconds,
        profile_id=args.profile_id,
    )

    try:
        server = RunnerServer(config)
    except RunnerAuthorityError as exc:
        print(f"ERROR=RUNNER_AUTHORITY_CHECK_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

    def _shutdown(signum: int, frame: Any) -> None:
        server.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    server.start()
    print(f"RUNNERD_STARTED=YES SOCKET_PATH={args.socket_path}")
    try:
        while True:
            server.handle_one_connection()
    finally:
        server.close()


if __name__ == "__main__":
    main()
