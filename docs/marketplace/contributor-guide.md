# Marketplace Contributor Guide

## Publish a Skill

### 1. Package Your Skill
```bash
mkdir -p my-skill/{src,tests,docs}
mv my_skill.py my-skill/src/
mv test_my_skill.py my-skill/tests/
```

### 2. Create Manifest
File: `my-skill/skill.json`
```json
{
  "id": "my-skill",
  "name": "My Custom Skill",
  "version": "0.1.0",
  "description": "Does amazing things",
  "source_url": "https://github.com/you/my-skill"
}
```

### 3. Submit to Marketplace
```bash
corvin marketplace publish my-skill/
```

### 4. Review Process
- Automatic: SHA256 verification
- Manual: Code review (3-5 days)
- Publication: Listed on Marketplace

## Versioning
- MAJOR: Breaking changes
- MINOR: New features
- PATCH: Bug fixes

Semantic versioning: MAJOR.MINOR.PATCH

## License
Skills must use Apache-2.0 or compatible license.
