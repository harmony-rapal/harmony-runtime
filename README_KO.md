# harmony runtime
![harmony runtime](docs/assets/logo.svg)

[English](README.md) · [비전](VISION_KO.md) · [핵심 개념](docs/CONCEPTS.md) · [구조](docs/ARCHITECTURE.md)

**AI는 제안하고, 사람은 승인하며, 선수는 실행하고, 증거는 입증합니다.**


**Big Tech builds better Players. Harmony builds a better Play.**

Harmony is a **MOK-centered operating layer for Human + AI + Service**, not
another multi-agent orchestrator. **You already have the Players. Now build a better Play.**

**Use the AI you already have. Give each Player the MOK it does best.**

Product direction: **MADANG → JUMUN → MOK → PLAYER → PLAY → ROK → REVIEW**.
MADANG is the shared field; JUMUN expresses intent; MOK makes the work contract explicit.
The current implementation is the governed runtime and bounded local demo described below.

하모니는 MOK 중심의 사람·AI·서비스 운영계층입니다. 마당에서 주문을 구체화하고 몫을 설계한 뒤 선수를 선택합니다. 아래 무료·기업 제공 정책은 출시 방향이며 현재 구현과 구분합니다.

## More Than

| Harmony | More than |
|---|---|
| MADANG | WORKSPACE — a shared field of intent, authority and work |
| JUMUN | PROMPT — an intent that becomes a work contract |
| MOK | SHARE + ROLE — outcome, scope, authority, capacity, done and evidence |
| PLAYER | AI + HUMAN + SERVICE — whoever can best carry out the MOK |
| ROK | LOG — connected work, decisions, evidence and outcomes for Review |

## MOK Designer · 제품 방향

**Don't start by choosing an AI. Design the MOK first.**

**MINIMUM INSTRUCTION, MAXIMUM CONTRACT.** Define WHAT, WHY, BOUNDARY,
CAPACITY, AUTHORITY, DONE, EVIDENCE and HANDOFF. Leave HOW to the Player.
MOK Designer is a product direction, not a shipped designer UI in this runtime.
User-facing work records are called **ROK**. Existing internal SILROK protocol
names, where present, remain stable; this language change does not migrate APIs or data.

## Personal Free와 Team / Enterprise · 출시 방향

**Free for individuals. Bring your own Players.**

개인판은 BYO AI·local-first를 지향합니다. 사용자의 AI 계정·API·로컬 모델을 각 제공자의 지원 방식과 약관 안에서 연결합니다. 핵심 MOK·Player·Evidence·Baton·ROK 경험은 무료 제공 방향이며 모델 추론 비용은 사용자가 부담합니다. 현재 제공되는 것은 로컬 소스 runtime과 데모이며, 완성된 Personal 제품이나 모든 AI 연동이 아닙니다.

**Your infrastructure. Your policy. Your ROK.**

**Your Players may belong to them. The Play belongs to you.**

Team / Enterprise는 self-host·VPC·on-prem 독립 설치를 지향합니다. 개인의 핵심 경험을 제한하지 않고 조직 복잡도·공유 ROK·정책·RBAC/SSO·지원/SLA에서 과금하는 방향입니다. 아직 출시된 요금제·검증된 기업 설치 패키지·SLA 약속이 아닙니다. 독립 설치에서는 Harmony Cloud·ROK 업로드·필수 telemetry 없이 운영하는 것을 목표로 합니다. AI 제공자로의 데이터 전송은 선택한 Player에 따라 달라집니다. Docker 데모 패키지를 기업 배포 제품으로 보아서는 안 됩니다.


## 더 나은 Player, 더 나은 Play

제품 비전은 **MADANG → JUMUN → MOK → PLAYER → PLAY → ROK → REVIEW**입니다. 먼저 업무의
목표·범위·권한·필요한 증거를 정하고, Human·AI·Service 가운데 적합한 Player를
선택합니다. 실행의 증거와 기록(ROK)을 리뷰해 다음 MOK과 Player 선택을 개선합니다.

Player 선택에는 능력, 권한, 개인정보 경계, 비용과 capacity가 함께 필요합니다.
남은 quota는 선택 신호이며 실행 권한을 부여하지 않습니다. 자동 Player Pool routing,
Challenge workflow, ROK 분석과 Review 기반 학습은 향후 방향이며 현재 구현 기능이
아닙니다. [비전](VISION_KO.md)을 확인하세요.

## 현재: AI-agent 개발팀 1–10명을 위한 실행 거버넌스

AI 명령을 권한 있는 터미널에 복사해서 실행하는 팀을 위한 데모입니다. AI가 제안하고 사람이 결정하며, 승인자·실행 identity·결과 evidence를 확인합니다.

## 60초 Baton 데모

저장소 루트에서 Python 3.12로 실행합니다. 추가 패키지나 credential은 필요 없습니다.

```bash
python3 -m demo.server
```

http://localhost:8765/demo.html 에서 REJECT → New proposal → APPROVE를 체험하세요. 거절은 파일을 만들지 않으며, 승인은 임시 sandbox에 고정 파일 하나만 만듭니다. 내용 hash, exit status, FINAL receipt를 확인할 수 있습니다. Ctrl+C로 종료하면 임시 파일을 정리합니다.

`docker compose up --build` 구성도 포함했습니다. HQ02에는 Docker가 없어 Docker 실행은 미검증입니다. 위 Python 실행은 검증된 fallback입니다.

모바일 형태 UI는 로컬 데모 서버에 연결된 구현입니다. 설치형 PWA·원격 휴대폰 승인·cloud 서비스는 구현되지 않았습니다. 정적 페이지는 실행할 수 없는 시각적 preview입니다. 데모 identity는 인증된 사람이 아니며 receipt는 서명된 telegraph 운영 receipt가 아닙니다. 단일 사용자용이며 공개 서버로 노출하지 마세요.

**FULL_RELEASE_ACTIVATION=HOLD**를 유지합니다. [검증 보고서](docs/DEMO_001_EVIDENCE.md).

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
| Madang (마당) | 사람·AI·서비스가 의도·권한·업무를 연결하는 공동의 마당. |
| Mok (몫) | 각 주체가 가진 책임과 권한의 경계. |
| Player (선수) | 실행을 담당하는 능동적 주체. |
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

## Deployment and roadmap

Today: local Python runtime and sandbox demo; Docker demo packaging is included
but execution remains unverified on HQ02. The static homepage is `docs/index.html`.

Planned: Personal Free (BYO AI / local-first), Team collaboration and independent
Enterprise installation (self-host / VPC / on-prem), shared ROK, policy and RBAC / SSO.
Worker pinning and canonical output evidence remain release blockers.
See [known limitations](KNOWN_LIMITATIONS.md) and [public release preparation](docs/PUBLIC_RELEASE.md).

## Community — 커뮤니티
소스 관련 논의는 저장소 이슈를 이용하세요. 연락처: [harmony.rapal@gmail.com](mailto:harmony.rapal@gmail.com).
운영 자료를 공유하기 전에 [보안 안내](SECURITY.md)를 확인하세요.

## 브랜딩과 라이선스
유영국의 기하학적 추상에서 영감을 받은 독자적인 h 마크와 풍경입니다. 원작 복제나 공식 제휴를 주장하지 않습니다. [브랜드 안내](docs/BRAND.md).
코드와 문서는 [Apache License 2.0](LICENSE)을 적용합니다. [NOTICE](NOTICE)를 확인하세요. 상표권 사용 허가는 포함하지 않으며 브랜드 사용은 [브랜드 안내](docs/BRAND.md)를 따릅니다.

