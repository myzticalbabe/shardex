# SHARDEX

Shard tooling: build a shard, render its mesh, and check the result rather than
trusting a digest that was written by the same code that wrote the file.

The full documentation lives in **[SHARDEX.md](SHARDEX.md)**; this file exists so
that a repository page has something to render.

## What ships here

| command | module | what it does |
|---|---|---|
| `shardex` | `shardex.py` | builds and inspects a shard |
| `shardex-mesh` | `meshscene.py` | renders the mesh scene for a shard |
| `shardex-transubstantiate` | `transubstantiate.py` | converts between shard representations |

`ops.py` ships as a module because `meshscene.py` imports it directly; omitting it
would break all three commands at import time.

## Requirements beyond pip

The mesh renderer drives a browser, so it needs **Node with puppeteer and a Chrome
install**. That cannot be expressed in `dependencies`, and claiming an empty
dependency set while silently requiring a browser is the kind of metadata lie this
tree has already been burned by. The Python side is standard library only.

## Quickstart

```bash
pip install .
shardex --help
```

## License

CDDL-1.1. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
License ruling: commit b49dfc2fef0178d1984437de577bfa00de8a4c32 (2026-11-03); see docs/LICENSE_PRECEDENT_CDDL11_2026-11-03.md.
