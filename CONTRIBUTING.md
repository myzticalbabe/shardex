# Contributing to Project SHARDEX

Thank you for considering a contribution to Project SHARDEX!

## Quick Start

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-change`
3. Make your changes
4. Run the test suite: `python -m pytest tests/ -v`
5. Submit a pull request

## Code Standards

- **License**: All contributions must be compatible with CDDL-1.1 (the project license)
- **Python**: 3.10+ (stdlib-only for L0/L1 paths; L2/L3 require Node + puppeteer + Chrome)
- **Style**: Follow the existing code style in the repository
- **Tests**: New functionality must include tests
- **Documentation**: Update relevant docs (SHARDEX.md, README.md) with your changes

## What We Accept

- Bug fixes in the mirror/shatter/forge pipeline
- Improvements to content-addressed sharding
- Enhancements to the FLAME arena forging
- Improvements to the browser-based `flame.html` shell
- Documentation improvements
- Test coverage improvements

## What We Don't Accept

- Changes that add Python runtime dependencies to L0/L1 (mirror, shatter are stdlib-only)
- Changes that break the "honest boundary" — SHARDEX does not extract a game's rules
- Changes that add external script/font/stylesheet references to `flame.html`

## The Pipeline

Contributions should be aware of the five rungs:

1. **Mirror** (`shardex.py`) — Mirror a codebase into content-addressed storage
2. **Shatter** (`ops.py`) — Shatter into content-addressed shards
3. **Forge** (`meshscene.py`) — Forge a shard into a 3D scene
4. **Transubstantiate** (`transubstantiate.py`) — Transubstantiate into FLAME arena
5. **Tune/Verify** — Tune parameters, verify golden state hash

## The FLAME Shell

The forged `flame.html` runs on the browser's own JavaScript with no
external script, font, or stylesheet reference. This is a deliberate
design constraint.

## Fidelity Statement

SHARDEX does not extract a game's rules. It derives a deterministic
arena from a shard's own content hash and provides a golden state
hash that independent implementations must agree on. SHARDEX.md's
"honest boundary" section governs what may be claimed.

## Pull Request Process

1. Ensure all tests pass
2. The maintainers will review and merge

## Reporting Issues

Use the GitHub issue tracker. Please include:
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version, Node/Chrome for L2/L3)

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## Security

See [SECURITY.md](SECURITY.md) for reporting security vulnerabilities.

## License

By contributing, you agree that your contributions will be licensed under CDDL-1.1.