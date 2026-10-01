# Public release preparation
Mission: HARMONY-RUNTIME-PUBLIC-RELEASE-001

## Preserved baseline
`main` was inspected at `0990117303d98cb78805d6a1dbfff6121b8644b3`.
Only documentation, website and branding paths are changed. All `telegraph/`, `tests/` and `examples/` blob identities must match this baseline.

## GitHub Pages
The site is dependency-free static HTML/CSS/SVG, under `docs/`.
After merging the packaging branch, configure repository **Settings → Pages → Deploy from a branch → main → /docs**.
Expected project URL after activation: https://harmony-rapal.github.io/harmony-runtime/
This URL is a planned destination, not a verified live deployment.

The `.nojekyll` file prevents Jekyll processing. All site asset paths are relative, so the project subpath works. No build workflow or external scripts are required.

## Remaining public-source release gates
- Apache License 2.0 was selected by the owner and added as LICENSE, with NOTICE and bilingual README references.
- Verify material implementation influences from project records, as required by [INFLUENCES.md](../INFLUENCES.md).
- Review the full repository for private operational material before changing visibility. The preserved example currently names `/home/r200dev/harmony-telegraph`; confirm this host path is acceptable for public distribution or authorize a separate example sanitization.
- Merge the reviewed packaging changes and configure Pages.
- Verify the published site after activation.

Repository visibility and Pages configuration require an administration capability. Connector-reported admin permission alone does not establish that the available tools can perform those settings changes.

## Production activation remains separate
[RELEASE_STATUS.md](../RELEASE_STATUS.md) continues to report full activation HOLD. Worker pinning and canonical output evidence remain tracked in [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md).
