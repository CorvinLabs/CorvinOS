# CorvinOS Ollama Release Guide

This guide explains how to prepare and publish CorvinOS on Ollama.

## Prerequisites

1. **Ollama Account**: Access to the Ollama package registry
2. **Verified Package**: All E2E tests passing on CI/CD
3. **Release Manager**: Someone with access to CorvinOS GitHub + Ollama registry
4. **Signature Key**: GPG key for signing releases (optional but recommended)

## Pre-Release Checklist

### 1. Verify All Tests Pass

```bash
# Run all installer tests
pytest tests/test_installer_*.py -v

# Run on each platform
# Linux
python -m pytest tests/ --cov=corvinOS

# macOS
pytest tests/ -k "Darwin or test_installer"

# Windows
pytest tests/ -k "Windows or test_installer"
```

### 2. Update Version Numbers

Edit version in:
- `pyproject.toml`: `version = "0.1.0"`
- `corvinOS/installer/__init__.py`: `__version__ = "0.1.0"`
- `ollama-manifest.yaml`: `version: "0.1.0"`
- `docs/INSTALL-UNIVERSAL.md`: Update first-time install commands

### 3. Build Wheels

```bash
# Build on each platform
python build_wheels.py

# Or use CI/CD
# GitHub Actions will auto-build wheels on:
# - Linux (ubuntu-latest, Python 3.9/3.10/3.11/3.12/3.13)
# - macOS (macos-latest, Python 3.9/3.11/3.13)
# - Windows (windows-latest, Python 3.9/3.11/3.13)

# Verify wheels
ls -lah dist/
# Expected:
#   corvinOS-0.1.0-py3-none-any.whl
#   corvinOS-0.1.0.tar.gz (source distribution)
```

### 4. Test Installation from Wheels

```bash
# Create fresh venv
python -m venv test_env
source test_env/bin/activate  # or test_env\Scripts\activate on Windows

# Install wheel
pip install dist/corvinOS-0.1.0-py3-none-any.whl

# Test
corvin-install --help
python -c "from corvinOS.installer.core import CorvinInstaller; print('✓ Import OK')"

# Cleanup
deactivate
rm -rf test_env
```

### 5. Create GitHub Release

```bash
# Tag the release
git tag -a v0.1.0 -m "CorvinOS 0.1.0 — Universal Installer Release"

# Push tag
git push origin v0.1.0

# Create release with attached wheels (via GitHub CLI)
gh release create v0.1.0 dist/*.whl dist/*.tar.gz \
  --title "CorvinOS 0.1.0" \
  --notes-file docs/RELEASE-NOTES-0.1.0.md
```

## Publishing to PyPI

### 1. Upload to PyPI Test (Recommended First)

```bash
# Install twine
pip install twine

# Upload to test.pypi.org (NOT production yet)
twine upload --repository testpypi dist/*

# Test installation from test.pypi
pip install -i https://test.pypi.org/simple/ corvinOS==0.1.0

# Verify
corvin-install --version
```

### 2. Upload to Production PyPI

```bash
# Only after testing from test.pypi works!
twine upload dist/*

# Verify
pip install corvinOS==0.1.0
corvin-install --version
```

## Publishing to Ollama

### 1. Prepare Ollama Package

```bash
# Create ollama package metadata
cat > .ollama/package.yaml << 'EOF'
name: corvinOS
version: "0.1.0"
description: "Universal CorvinOS installer"
homepage: "https://corvin.ai"
repository: "https://github.com/CorvinLabs/CorvinOS"
license: "Apache-2.0"

# Points to PyPI package
pypi_package: "corvinOS"
pypi_version: "0.1.0"

# Ollama integration
ollama:
  install_command: "pip install corvinOS==0.1.0"
  entry_point: "corvin-install"
  requires_node: true  # npm install needs Node.js
EOF
```

### 2. Submit to Ollama Registry

```bash
# Authentication
ollama auth login

# Submit package
ollama package publish .ollama/package.yaml

# Verify
ollama package info corvinOS
ollama package versions corvinOS
```

### 3. Verify Ollama Installation

```bash
# Install via Ollama
ollama install corvinOS

# Test
corvin-install --help

# Run setup
corvin-install --yes
```

## Post-Release

### 1. Announce Release

- GitHub Releases page
- CorvinOS website/blog
- Community channels (Discord, Reddit, etc.)

### 2. Monitor Issues

- Watch GitHub Issues for bug reports
- Respond to Ollama package reviews/feedback
- Track CI/CD status on new commits

### 3. Maintenance

- Keep dependencies updated
- Security audits quarterly
- Release patches as needed (0.1.1, 0.1.2, etc.)

## Troubleshooting

### PyPI Upload Fails

```bash
# Verify wheels are valid
twine check dist/*

# Check __version__ matches pyproject.toml
grep "version" pyproject.toml
grep "__version__" corvinOS/installer/__init__.py

# Try verbose upload
twine upload -r pypi dist/* -v
```

### Ollama Installation Fails

```bash
# Check Ollama logs
ollama logs corvinOS

# Verify PyPI package is accessible
pip install corvinOS==0.1.0

# Check Node.js is installed
node --version
npm --version
```

### Test.PyPI vs Production Mismatch

```bash
# Clear pip cache
pip cache purge

# Reinstall from correct repo
pip uninstall corvinOS
pip install -i https://test.pypi.org/simple/ corvinOS==0.1.0  # Or just pypi
```

## Version Bumping Strategy

Follow semantic versioning:

- **0.1.0** → **0.1.1**: Bug fixes
- **0.1.0** → **0.2.0**: New features (backward compatible)
- **0.1.0** → **1.0.0**: Breaking changes

For each version:

1. Update version in `pyproject.toml`, `__init__.py`, `ollama-manifest.yaml`
2. Update `CHANGELOG.md` with changes
3. Run full test suite
4. Create GitHub tag + release
5. Upload to PyPI
6. Update Ollama package

## Security Considerations

1. **Signing**: Sign wheels with GPG (recommended for production)
2. **Checksums**: Publish SHA256 checksums
3. **SBOM**: Consider generating Software Bill of Materials (SBOM)
4. **Auditing**: Regular security audits before major releases

## Support

- **Issues**: https://github.com/CorvinLabs/CorvinOS/issues
- **Discussions**: https://github.com/CorvinLabs/CorvinOS/discussions
- **Security**: https://github.com/CorvinLabs/CorvinOS/security
