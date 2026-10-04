# harmony runtime — 비전

**Big Tech builds better Players. Harmony builds a better Play.**

**MADANG → JUMUN → MOK → PLAYER → PLAY → ROK → REVIEW**. 업무에서 출발합니다.
마당에서 목표를 구체화하고, 범위가 정해진 MOK으로 나눕니다. 업무가 요구하는
능력·권한·개인정보 경계·비용·capacity·증거 조건에 맞는 Player를 선택합니다.
Player는 승인된 범위에서 Play를 수행합니다. Baton은 권한과 증거를 연결하고,
Challenge는 증거가 부족한 결과를 재검토하는 장치가 됩니다. ROK은 업무 기록을
남기며, Review는 다음 MOK과 Play를 개선합니다.

Player Pool은 **Human + AI + Service**를 포함할 수 있습니다. 동료와 전문가,
Claude·Codex·Gemini·주권 AI·전문 AI, 승인된 서비스가 후보입니다. 이는 제품 방향의
예시이며 구현된 연동이나 파트너십을 뜻하지 않습니다. 남은 quota는 capacity 신호이고,
실행 권한을 부여하지 않습니다.

개발을 넘어 디자인·리서치·영업·운영의 업무까지 연결하는 것이 비전입니다.
현재 출발점은 1–10명 AI-agent 개발팀을 위한 로컬 Baton 데모입니다. 사람의 결정과
sandbox 파일 하나의 실행은 구현되어 있지만, Player Pool 자동 routing·Challenge
workflow·ROK 분석·Review 기반 학습은 이번 릴리스의 구현 기능이 아닙니다.

학습은 최소한의 운영 메타데이터와 사용자가 동의한 신호를 바탕으로 해야 합니다.
고객 코드나 원문 프롬프트의 중앙 수집을 전제하지 않습니다. 이번 릴리스에는
telemetry 수집을 추가하지 않습니다.

“마지막 토큰 한 개까지, 너의 몫을 다해라”

Personal Free·Team·독립 Enterprise 설치는 향후 계획입니다.
FULL_RELEASE_ACTIVATION=HOLD를 유지합니다. 운영 준비 완료나 compliance 인증을
주장하지 않습니다.

[핵심 개념](docs/CONCEPTS.md) · [English](VISION.md)

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
