# Manifest Notes

## Project Manifest: Shardex v0.1.0

### Overview
This document records the provenance and integrity information for the Shardex release.

### Source Provenance

- **Source Repository**: https://github.com/myzticalbabe/shardex
- **Parent Project**: AZAZAEL (https://github.com/myzticalbabe/azazael)
- **Extraction Date**: 2026-11-18
- **Extraction Method**: Manual extraction from AZAZAEL trunk (Project_SHARDEX directory)
- **Git Commit**: HEAD at extraction (to be filled on release)

### Integrity Verification

#### File Hashes (SHA-256)
To be generated at release time:
```bash
# Generate manifest hashes
find . -type f -name "*.py" -o -name "*.md" -o -name "*.txt" -o -name "*.json" -o -name "*.yml" -o -name "*.yaml" | \
  sort | \
  xargs sha256sum > MANIFEST_HASHES.txt
```

#### Verification Command
```bash
# Verify integrity
sha256sum -c MANIFEST_HASHES.txt
```

### Dependency Manifest

#### Runtime Dependencies
None (stdlib-only core)

#### Development Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| pytest | >=7.0 | Testing framework |
| pytest-cov | >=4.0 | Coverage reporting |
| pytest-xdist | >=3.0 | Parallel test execution |
| black | >=23.0 | Code formatting |
| ruff | >=0.1.0 | Linting |
| mypy | >=1.0 | Type checking |

#### Optional Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| sphinx | >=6.0 | Documentation generation |
| sphinx-rtd-theme | >=1.0 | Documentation theme |

### Build Manifest

#### Build Requirements
- Python 3.10+ (3.14t/3.15t/3.16t recommended)
- pip >= 23.0
- setuptools >= 68.0
- wheel >= 0.40.0

#### Build Commands
```bash
# Standard build
python -m build

# Development install
pip install -e ".[dev]"

# Free-threaded build (recommended)
/path/to/python3.16t -m build
```

### Distribution Manifest

#### PyPI Package
- **Name**: shardex
- **Version**: 0.1.0
- **Format**: sdist + wheel
- **Python Requires**: >=3.10

#### GitHub Release
- **Tag**: v0.1.0
- **Assets**: Source tarball, wheel, SBOM
- **SBOM Format**: SPDX JSON

### SBOM (Software Bill of Materials)

Generated with `cyclonedx-bom` or `syft`:
```bash
# Generate SBOM
syft packages dir:. -o spdx-json > shardex-sbom.spdx.json
cyclonedx-bom -o shardex-bom.json .
```

### Compliance Manifest

#### Licenses
- **Primary**: Soul Forge License v1.0 (LicenseRef-SoulForge-1.0) — SOUL LICENCE ONLY
- **Third-party**: MIT, BSD-3-Clause, PSF-2.0 (stdlib)

#### Standards Compliance
- ✅ Semantic Versioning (SemVer 2.0)
- ✅ Conventional Commits
- ✅ Keep a Changelog
- ✅ SPDX License Identifiers
- ✅ REUSE Specification (license headers)

### Release Checklist

- [ ] All tests pass on Linux, macOS, Windows
- [ ] All tests pass on Python 3.10-3.16t
- [ ] Free-threaded tests pass on 3.14t/3.15t/3.16t
- [ ] CHANGELOG.md updated
- [ ] Version bumped in pyproject.toml / __init__.py
- [ ] MANIFEST_HASHES.txt generated and verified
- [ ] SBOM generated
- [ ] PyPI package published
- [ ] GitHub release created with assets
- [ ] Documentation deployed
- [ ] Security scan passed (pip-audit, safety)

### Contact

For manifest questions: maintainers@shardex.dev