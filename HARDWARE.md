# Hardware Requirements

## Minimum Requirements

| Component | Specification |
|-----------|---------------|
| CPU | 2 cores, x86_64 or ARM64 |
| RAM | 2 GB |
| Storage | 500 MB free space |
| OS | Linux, macOS, Windows 10+ |
| Python | 3.10+ |

## Recommended Requirements

| Component | Specification |
|-----------|---------------|
| CPU | 4+ cores, x86_64 or ARM64 |
| RAM | 8 GB |
| Storage | 2 GB free space (SSD preferred) |
| OS | Linux (Ubuntu 22.04+, Debian 12+, Fedora 38+) |
| Python | 3.12+ (3.14t/3.15t/3.16t free-threaded for optimal performance) |

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| Linux x86_64 | ✅ Fully supported | Primary development platform |
| Linux ARM64 | ✅ Supported | Tested on Raspberry Pi 4/5, Apple Silicon via Asahi |
| macOS x86_64 | ✅ Supported | Intel Macs |
| macOS ARM64 (Apple Silicon) | ✅ Supported | M1/M2/M3 native |
| Windows 10/11 x86_64 | ✅ Supported | WSL2 recommended for best experience |
| Windows ARM64 | ⚠️ Experimental | Limited testing |

## Free-Threaded Python

Shardex is optimized for free-threaded Python builds (3.14t/3.15t/3.16t):

```bash
# Check if your Python build is free-threaded
python -c "import sys; print('Free-threaded:', hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled())"
```

### Performance Characteristics

| Python Build | GIL | Parallel Speedup | Notes |
|--------------|-----|------------------|-------|
| Standard (3.10-3.13) | Yes | Limited by GIL | Threading for I/O only |
| Free-threaded (3.14t+) | No | Near-linear scaling | True parallelism for CPU-bound work |

## Container Requirements

```dockerfile
# Minimum container
FROM python:3.12-slim
RUN pip install shardex

# Optimized container (free-threaded)
FROM python:3.16t-slim
RUN pip install shardex
ENV PYTHONPATH=/app
```

## CI/CD Hardware

GitHub Actions runners used:
- `ubuntu-latest` (4 cores, 16 GB RAM) — primary
- `macos-latest` (3 cores, 14 GB RAM) — macOS testing
- `windows-latest` (4 cores, 16 GB RAM) — Windows testing

Self-hosted runners recommended for:
- ARM64 testing
- Free-threaded Python testing
- Performance benchmarking

## Known Hardware Issues

1. **Apple Silicon Rosetta 2**: Free-threaded Python under Rosetta may have stability issues. Use native ARM64 Python.

2. **Windows without WSL**: Native Windows Python has limited free-threading support. Use WSL2 Ubuntu for best results.

3. **Low Memory Systems**: < 2 GB RAM may cause OOM during large shard operations. Use `--max-workers` to limit parallelism.