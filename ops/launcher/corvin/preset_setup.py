"""Preset setup CLI — configure installation preset at install-time (Phase 6.5)."""

import argparse
from pathlib import Path
import yaml


def get_tenant_yaml_path() -> Path:
    """Get path to tenant.corvin.yaml."""
    home = Path.home()
    return home / ".corvin" / "tenants" / "_default" / "tenant.corvin.yaml"


def load_tenant_spec() -> dict:
    """Load tenant.corvin.yaml spec or return defaults."""
    path = get_tenant_yaml_path()
    if not path.exists():
        return {"preset": "standard"}

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return data.get("spec", {})
    except Exception:
        return {"preset": "standard"}


def save_tenant_spec(spec: dict) -> None:
    """Save updated spec to tenant.corvin.yaml."""
    path = get_tenant_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing or create new
    if path.exists():
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    # Update spec
    data["spec"] = spec

    # Write back
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Set CorvinOS installation preset (Phase 6.5)"
    )
    parser.add_argument(
        "preset",
        choices=["minimal", "standard", "advanced"],
        help="Installation preset to configure",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output messages",
    )

    args = parser.parse_args()

    # Load, update, save
    spec = load_tenant_spec()
    spec["preset"] = args.preset
    save_tenant_spec(spec)

    if not args.quiet:
        print(f"✓ Preset configured: {args.preset}")
        print(f"  File: {get_tenant_yaml_path()}")
        print("  (Restart CorvinOS for changes to take effect)")

    return 0


if __name__ == "__main__":
    exit(main())
