# Architecture
The preserved source baseline is `0990117303d98cb78805d6a1dbfff6121b8644b3`.

| Layer | Source | Responsibility |
|---|---|---|
| Packet | `telegraph/packet.py` | Strict schema and canonical packet identity |
| Ledger / Receipt | `telegraph/ledger.py`, `telegraph/receipt.py` | SQLite transitions, stage claims and receipt chains |
| Human Gate | `telegraph/human_gate.py` | Signed receipt verification with external allowed_signers authority |
| Actuator | `telegraph/actuator_service.py`, `telegraph/actuatord.py` | Authority boundary and Unix socket service |
| Runner | `telegraph/runner_service.py`, `telegraph/runnerd.py` | Governed execution boundary |
| Builder | `telegraph/builder_adapter.py`, `telegraph/builder_evidence.py` | Worker integration and evidence storage |
| CLI | `telegraph/cli.py` | Source-level inspection and lifecycle commands |

The philosophical concepts in [CONCEPTS.md](CONCEPTS.md) describe the intended collaboration model. They are not a one-to-one mapping to implemented classes.

Python 3.12 is the documented target. The Python source uses the standard library; governed execution additionally depends on a configured host, external trust roots, Unix authority settings and the pinned OpenSSH verifier. It is not a portable production installation.

The CLI exposes `approve-gate` with a receipt and an allowed-signers path. A plain approver string must not be represented as equivalent to verified production authority. Consult source and tests before provisioning a host.

Read [release status](../RELEASE_STATUS.md) and [known limitations](../KNOWN_LIMITATIONS.md). The packaging work does not alter the R5C-D2 verification scope or remove the full-release HOLD.

## 한국어
Packet은 임무 스키마와 정체성을 검증하고, Ledger와 Receipt는 상태 전이·실행 claim·증거 사슬을 기록합니다. Human Gate는 외부 신뢰 기준으로 서명된 승인 증빙을 검증합니다. Actuator와 Runner는 권한 및 실행 경계를 담당하며, Builder는 worker 연동과 증거 저장을 담당합니다.

운영 실행에는 Python 외에도 호스트 권한 설정, 외부 신뢰 기준, 고정된 OpenSSH 검증기 등이 필요합니다. 이번 작업은 문서·브랜딩 패키징이며 전체 운영 릴리스 HOLD를 해제하지 않습니다.
