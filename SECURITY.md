# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Do not report security vulnerabilities via public GitHub issues.**

Instead, please report them via email to **security@azazael.local**.

Include the following information:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any known workarounds

We will acknowledge receipt within 48 hours and provide a status update
within 5 business days.

## Security Model

SHARDEX is a code mirroring/shattering/forging pipeline with the following security properties:

1. **L0/L1 stdlib-only** — Mirror and shatter paths use only Python standard library
2. **L2/L3 require Node + puppeteer + Chrome** — Explicitly documented non-pip dependencies
3. **No network access in L0/L1** — Mirror/shatter do not make network requests
4. **Browser sandbox** — Forge runs in browser's own JavaScript with no external references
5. **No arbitrary code execution** — Does not eval or execute untrusted code from mirrored codebases

## Disclosure Policy

We follow coordinated disclosure. Once a vulnerability is confirmed and a
fix is available, we will:

1. Release a patch
2. Publish a security advisory
3. Credit the reporter (unless they request anonymity)

## Scope

This policy covers the SHARDEX pipeline: `shardex.py`, `ops.py`, `meshscene.py`,
`transubstantiate.py`, `third_impl.py`, `rank.py`, and the forged `flame.html`.