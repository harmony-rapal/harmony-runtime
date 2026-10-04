# Harmony positioning update — 2026-10-04

Base: `fb757a0d742f1f3ea8e4ca464c05188b893dc8eb` (`origin/main`).
Branch: `harmony/positioning-rok-20261004`.

## Changes

- README.md / README_KO.md: MOK-centered Human + AI + Service positioning, full work cycle, More Than, MOK Designer, Personal Free and independent Team / Enterprise direction.
- VISION.md / VISION_KO.md: matching product language and roadmap boundaries.
- docs/CONCEPTS.md: JUMUN, PLAY, ROK and REVIEW definitions and ROK naming policy.
- docs/index.html: requested hero copy, seven-step cycle, More Than, designer principles and planned offering sections.
- docs/style.css: wrapping and responsive grids for longer product copy.
- No runtime, tests, examples, protocol identifiers, API schemas or stored data changed. No SILROK occurrence existed in the inspected source; the compatibility policy preserves internal names where present.

## Verification

- Static site: dependency-free HTML/CSS/SVG, no build pipeline required.
- Local HTML/Markdown links and anchors: 76 checked, zero missing targets.
- External homepage links: seven unique HTTPS destinations returned HTTP 200. Quickstart now targets the existing main README demo anchor.
- Browser: changed page loaded; required copy and seven stages present in accessibility tree; images loaded. DOM measurements at 320, 375 and 1440 CSS px found no main-content elements beyond viewport and no document horizontal overflow. Mobile screenshots inspected. Local preview connectivity was intermittent; a later reload failed after successful checks. Viewport override restored.
- Python compileall: PASS for telegraph, tests and demo.
- git diff --check: PASS.
- Full unittest suite outside socket-restricted sandbox: 229 run, 228 passed, one error in `test_b4_profile_resolve_permitted_uid` because the host lacks the `harmony` Unix account. The same test fails identically on an extracted, unchanged origin/main. Do not create a system account as part of a copy change.

## Remaining gates

- GitHub Pages is built from main /docs at https://harmony-rapal.github.io/harmony-runtime/. Changes stay in the review branch; publication requires reviewed merge. No merge or Pages configuration change performed.
- Complete green runtime suite requires the documented host identity prerequisite for the remaining B4 test.
- Full production release activation remains HOLD. Personal Free, designer UI, automated Player routing, shared ROK / Review learning, enterprise installation, RBAC / SSO and paid support / SLA are roadmap directions, not newly implemented features.
- Docker execution remains unverified on HQ02.
