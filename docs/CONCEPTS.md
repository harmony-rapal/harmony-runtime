# MADANG · JUMUN · MOK · PLAYER · PLAY · ROK · REVIEW
[English README](../README.md) · [한국어 README](../README_KO.md)

These names express the product philosophy. They do not introduce additional runtime APIs.

| Concept | Meaning | 뜻 |
|---|---|---|
| Madang (마당) | A shared field where Human, AI and Service Players connect intent, authority and work. | 사람·AI·서비스가 의도·권한·업무를 연결하는 공동의 마당. |
| Mok (몫) | A boundary of responsibility and authority. | 각 주체가 가진 책임과 권한의 경계. |
| Player (선수) | An active participant who executes. | 실행을 담당하는 능동적 주체. |
| Baton (바통) | A trusted handoff of authority. | 신뢰를 기반으로 한 승인과 책임의 전달. |

## From a shared objective to accountable execution
In Madang, a human and AI can develop an objective. A Mok makes the scope, responsibility and authority explicit. A Player carries out the authorized work. A Baton represents a trusted handoff of authority, with approval and evidence connecting the handoff to its result.

For example, AI can propose a documentation change. A human authorizes its bounded scope; the executing participant makes the change; the diff and verification results provide evidence. This is a conceptual example, not an automated workflow supplied by this release.

## 공동 목표에서 책임 있는 실행으로
마당에서 사람과 AI가 목표를 함께 구체화합니다. 몫은 범위·책임·권한을 명확히 합니다. 선수는 승인된 작업을 실행합니다. 바통은 신뢰를 기반으로 권한을 전달하고, 승인과 증거를 통해 전달과 결과를 연결합니다.

예를 들어 AI가 문서 수정을 제안하면 사람이 한정된 범위를 승인하고, 실행 주체가 수정하며, 변경 내역과 검증 결과가 증거가 됩니다. 이는 개념 설명이며 이번 릴리스에 포함된 자동화 기능을 뜻하지 않습니다.

## Work cycle

**MADANG → JUMUN → MOK → PLAYER → PLAY → ROK → REVIEW**

JUMUN (주문) expresses human intent before it is shaped into bounded MOKs.
PLAY is authorized execution; ROK connects work, decisions and evidence; REVIEW improves the next MOK.
ROK is the user-facing name; existing internal SILROK protocol identifiers remain stable where present.

## More Than

| Harmony | More than |
|---|---|
| MADANG | WORKSPACE — a shared field of intent, authority and work |
| JUMUN | PROMPT — an intent that becomes a work contract |
| MOK | SHARE + ROLE — outcome, scope, authority, capacity, done and evidence |
| PLAYER | AI + HUMAN + SERVICE — whoever can best carry out the MOK |
| ROK | LOG — connected work, decisions, evidence and outcomes for Review |

## MOK Designer · Product direction

**Don't start by choosing an AI. Design the MOK first.**

**MINIMUM INSTRUCTION, MAXIMUM CONTRACT.** Define WHAT, WHY, BOUNDARY,
CAPACITY, AUTHORITY, DONE, EVIDENCE and HANDOFF. Leave HOW to the Player.
MOK Designer is a product direction, not a shipped designer UI in this runtime.
User-facing work records are called **ROK**. Existing internal SILROK protocol
names, where present, remain stable; this language change does not migrate APIs or data.
