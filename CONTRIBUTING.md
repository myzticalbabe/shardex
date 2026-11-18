# Contributing to Shardex

## How to Contribute

### Reporting Issues
- Use the bug report template (.github/ISSUE_TEMPLATE/bug_report.yml)
- Include reproduction steps, expected vs actual behavior
- Specify Python version, OS, and shardex version

### Pull Request Process
1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes with tests
4. Ensure all tests pass: `python -m pytest -v`
5. Update CHANGELOG.md
6. Submit PR with description of changes

### Coding Conventions
- **Stdlib-first**: Prefer stdlib over external dependencies
- **Thread safety**: Use proper locking for concurrent access
- **Docstrings**: All public methods must have docstrings with Args/Returns
- **Type hints**: Use type annotations for all public APIs

### Test Organization
```
tests/
  test_shardex_*.py    # Shardex core tests
  test_shard_*.py      # Shard execution tests
  test_registry_*.py   # Registry tests
```

### Running Tests
```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest --cov=shardex tests/ -v
```

### Pull Request Checklist
- [ ] Tests written and passing
- [ ] Docstrings added for public APIs
- [ ] Type hints added
- [ ] CHANGELOG.md updated
- [ ] No breaking changes without version bump