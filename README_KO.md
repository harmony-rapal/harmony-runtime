# harmony runtime
![harmony runtime](docs/assets/logo.svg)

[English](README.md) · [비전](VISION_KO.md) · [핵심 개념](docs/CONCEPTS.md) · [구조](docs/ARCHITECTURE.md)

**AI는 제안하고, 사람은 승인하며, 선수는 실행하고, 증거는 입증합니다.**

## Vision — 비전
사람의 의도, 한정된 권한, 실행과 증거를 연결하는 공동 작업 공간을 지향합니다.

> 마지막 토큰 한 개까지, 너의 몫을 다해라

## Why harmony — 하모니가 필요한 이유
AI와 협업할 때는 책임의 경계와 검증 가능한 전달이 필요합니다. harmony runtime은 승인된 임무 패킷, 실행 claim, 연결된 receipt를 위한 결정적이고 fail-closed한 기반을 개발합니다.

## 현재 상태
현재는 개발 기준 소스입니다. [R5C-D2 증거 패키지는 PASS](RELEASE_STATUS.md)이며 **전체 운영 릴리스 활성화는 HOLD**입니다. worker 자동 업데이트와 정식 출력 증거를 포함한 [알려진 한계](KNOWN_LIMITATIONS.md)를 확인하세요. 공개 소스 패키징이 운영 준비 완료를 의미하지 않습니다.

## Core Concepts — 핵심 개념
| 개념 | 뜻 |
|---|---|
| Madang (마당) | 사람과 AI가 함께 만나는 열린 작업 공간. |
| Mok (몫) | 각 주체가 가진 책임과 권한의 경계. |
| Sunsu (선수) | 실행을 담당하는 능동적 주체. |
| Baton (바통) | 신뢰를 기반으로 한 승인과 책임의 전달. |

제품 철학을 표현하는 용어이며 추가 구현 API를 뜻하지 않습니다. [한영 개념 안내](docs/CONCEPTS.md).

## Architecture — 구조
패킷 검증, SQLite ledger, receipt 사슬, 서명된 Human Gate 승인 검증, Actuator·Runner 경계, builder 연동과 증거 저장을 포함합니다. [구조 안내](docs/ARCHITECTURE.md).

runtime 코드 기준은 `0990117303d98cb78805d6a1dbfff6121b8644b3`로 보존합니다. 문서상 대상은 Python 3.12이며, 운영 실행에는 호스트 권한 설정·외부 신뢰 기준·고정된 OpenSSH 검증기가 필요합니다.

## 시작하기
저장소 루트에서 실행하세요. CLI를 사용하기 위한 Python 패키지 설치는 필요하지 않습니다.

```bash
python3 -m telegraph --help
python3 -m telegraph ingest examples/mission.json --state-dir /tmp/harmony-demo
# ingest가 반환한 PACKET_SHA256을 사용하세요:
python3 -m telegraph show <PACKET_SHA256> --state-dir /tmp/harmony-demo
python3 -m telegraph verify <PACKET_SHA256> --state-dir /tmp/harmony-demo
```

패킷 입력·조회 예제입니다. worker 실행이나 운영 승인을 수행하지 않습니다. 운영 실행 전에 소스와 호스트 요구 사항을 검토하고 상태 파일과 자격 증명은 저장소 외부에 보관하세요.

## 검증
```bash
python3 -m unittest discover -v
python3 -m compileall -q telegraph tests
```

## Deployment — 배포
| 방향 | 상태 |
|---|---|
| Personal Cloud — 개인 클라우드 | Coming soon |
| Team Cloud — 팀 협업 클라우드 | Coming soon |
| Independent Server — 독립 서버 | Coming soon |

정적 홈페이지는 `docs/index.html`입니다. GitHub Pages 설정과 남은 공개 조건은 [공개 준비 안내](docs/PUBLIC_RELEASE.md)에 정리합니다.

## Roadmap — 향후 계획
- 봉인된 실행 동안 worker 버전을 고정합니다.
- 제한된 worker 출력을 정식 증거에 연결합니다.
- Personal Cloud, Team Cloud, Independent Server 배포 경로를 개발합니다.

모두 향후 방향이며 제공 완료된 기능이 아닙니다.

## Community — 커뮤니티
소스 관련 논의는 저장소 이슈를 이용하세요. 연락처: [harmony.rapal@gmail.com](mailto:harmony.rapal@gmail.com).
운영 자료를 공유하기 전에 [보안 안내](SECURITY.md)를 확인하세요.

## 브랜딩과 라이선스
유영국의 기하학적 추상에서 영감을 받은 독자적인 h 마크와 풍경입니다. 원작 복제나 공식 제휴를 주장하지 않습니다. [브랜드 안내](docs/BRAND.md).
현재 LICENSE 파일이 없습니다. 오픈소스 라이선스를 가정하지 않으며 라이선스 결정은 공개 전 남은 조건입니다.
