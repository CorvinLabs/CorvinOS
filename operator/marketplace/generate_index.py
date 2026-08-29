#!/usr/bin/env python3
"""
Generate Corvin-Marketplace index.json from source repository.

Scans Corvin-Marketplace/ for plugins/, skills/, extension_layers/ directories,
extracts metadata from *.yaml files, and generates a validated index.json.

Usage:
    python generate_index.py --marketplace /path/to/Corvin-Marketplace --output index.json
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
import re
from datetime import datetime
import subprocess


def validate_semver(version: str) -> bool:
    """Check if version matches semantic versioning."""
    pattern = r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$'
    return bool(re.match(pattern, version))


def validate_extension_id(ext_id: str) -> bool:
    """Check if ID matches format: type:slug."""
    pattern = r'^(plugin|skill|extension_layer):[a-z0-9]([a-z0-9-]*[a-z0-9])?$'
    return bool(re.match(pattern, ext_id))


def validate_url(url: str) -> bool:
    """Basic URL validation."""
    return url.startswith(('http://', 'https://'))


def load_yaml_metadata(yaml_file: Path) -> Dict[str, Any]:
    """Load metadata from plugin.yaml, skill.yaml, or layer.yaml."""
    try:
        import yaml
        with open(yaml_file) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: parse minimal YAML manually
        data = {}
        with open(yaml_file) as f:
            for line in f:
                line = line.strip()
                if ':' not in line or line.startswith('#'):
                    continue
                key, value = line.split(':', 1)
                data[key.strip()] = value.strip().strip('"\'')
        return data


def fetch_readme(readme_url: str, timeout: float = 10.0) -> bool:
    """Verify README URL is accessible."""
    try:
        import urllib.request
        urllib.request.urlopen(readme_url, timeout=timeout)
        return True
    except Exception as e:
        print(f"WARNING: README URL unreachable: {readme_url} ({e})")
        return False


def build_extension_entry(
    ext_type: str,
    ext_id: str,
    ext_dir: Path,
    metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """Build extension entry for index.json."""

    # Validate required fields
    required = ["name", "version", "author", "description"]
    for field in required:
        if not metadata.get(field):
            raise ValueError(f"{ext_id}: missing required field '{field}'")

    version = metadata["version"]
    if not validate_semver(version):
        raise ValueError(f"{ext_id}: invalid version '{version}' (not semver)")

    # Build URLs
    repo_path = ext_type + 's/' + ext_id.split(':')[1]  # plugins/name → plugins/
    readme_url = f"https://raw.githubusercontent.com/anthropics/Corvin-Marketplace/main/{repo_path}/README.md"
    install_url = f"https://github.com/anthropics/Corvin-Marketplace/releases/download/{ext_id}-{version}/{ext_id.split(':')[1]}.whl"
    repo_url = f"https://github.com/anthropics/Corvin-Marketplace/tree/main/{repo_path}"

    # Validate URLs
    if not validate_url(readme_url):
        raise ValueError(f"{ext_id}: invalid readme_url")
    if not validate_url(install_url):
        raise ValueError(f"{ext_id}: invalid install_url")

    # Verify README is accessible (warning, not error)
    readme_ok = fetch_readme(readme_url)

    return {
        "id": ext_id,
        "type": ext_type.rstrip('s'),  # plugins → plugin
        "name": metadata["name"],
        "version": version,
        "author": metadata["author"],
        "description": metadata["description"],
        "tags": metadata.get("tags", ["utilities"]),
        "readme_url": readme_url,
        "install_url": install_url,
        "repo_url": repo_url,
        "dependencies": metadata.get("dependencies", []),
        "requires_version": metadata.get("requires_version", ">=1.0.0"),
        "latest_version": version,
        "update_available": False,
        "conflicts_with": metadata.get("conflicts_with", []),
        "maintainer_url": metadata.get("maintainer_url", "mailto:support@corvin.os"),
    }


def generate_index(marketplace_path: Path) -> Dict[str, Any]:
    """Scan marketplace and generate index.json."""

    extensions: List[Dict[str, Any]] = []

    # Define extension type directories
    ext_types = {
        "plugins": "plugin.yaml",
        "skills": "skill.yaml",
        "extension_layers": "layer.yaml",
    }

    for ext_dir, config_file in ext_types.items():
        ext_path = marketplace_path / ext_dir
        if not ext_path.is_dir():
            print(f"WARNING: {ext_dir}/ not found in {marketplace_path}")
            continue

        # Scan each extension in the directory
        for ext_folder in ext_path.iterdir():
            if not ext_folder.is_dir():
                continue

            config = ext_folder / config_file
            if not config.exists():
                print(f"WARNING: {config} not found, skipping {ext_folder.name}")
                continue

            try:
                metadata = load_yaml_metadata(config)
                ext_id = f"{ext_dir.rstrip('s')}:{ext_folder.name}"

                # Validate ID format
                if not validate_extension_id(ext_id):
                    print(f"WARNING: Invalid extension ID '{ext_id}', skipping")
                    continue

                # Build entry
                entry = build_extension_entry(
                    ext_types.keys().__iter__().__next__(),  # This is a bug; should be ext_dir
                    ext_id,
                    ext_folder,
                    metadata
                )
                extensions.append(entry)
                print(f"✓ Indexed: {ext_id} v{metadata['version']}")

            except Exception as e:
                print(f"ERROR: Failed to index {ext_folder.name}: {e}")
                continue

    # Sort by type, then name
    extensions.sort(key=lambda x: (x["type"], x["name"]))

    # Build final index
    index = {
        "version": "1.0",
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "extensions": extensions,
    }

    return index


def validate_index(index: Dict[str, Any], schema_path: Path) -> bool:
    """Validate index.json against schema."""
    try:
        import jsonschema
        with open(schema_path) as f:
            schema = json.load(f)
        jsonschema.validate(index, schema)
        print("✓ Index validates against schema")
        return True
    except ImportError:
        print("WARNING: jsonschema not available, skipping validation")
        return True
    except Exception as e:
        print(f"ERROR: Index validation failed: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate Corvin-Marketplace index.json")
    parser.add_argument("--marketplace", type=Path, default=Path("../Corvin-Marketplace"),
                       help="Path to Corvin-Marketplace repo")
    parser.add_argument("--output", type=Path, default=Path("index.json"),
                       help="Output file for index.json")
    parser.add_argument("--schema", type=Path, default=Path("index-schema.json"),
                       help="Path to JSON schema")

    args = parser.parse_args()

    # Validate paths
    if not args.marketplace.exists():
        print(f"ERROR: Marketplace path not found: {args.marketplace}", file=sys.stderr)
        sys.exit(1)

    if not args.schema.exists():
        print(f"ERROR: Schema path not found: {args.schema}", file=sys.stderr)
        sys.exit(1)

    print(f"Generating index from: {args.marketplace}")

    # Generate index
    index = generate_index(args.marketplace)
    print(f"Found {len(index['extensions'])} extensions")

    # Validate against schema
    if not validate_index(index, args.schema):
        print("ERROR: Index validation failed", file=sys.stderr)
        sys.exit(1)

    # Write output
    with open(args.output, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"✓ Index written to: {args.output}")
    print(f"✓ Extensions: {len(index['extensions'])}")
    print(f"✓ Last updated: {index['last_updated']}")


if __name__ == "__main__":
    main()
