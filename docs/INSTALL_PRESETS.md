# CorvinOS Installation Presets (Phase 6.5)

When installing CorvinOS, you can choose an **installation preset** that determines which feature tiers are enabled by default for new users.

## Usage

```bash
# Install with a specific preset
curl -fsSL https://corvin-labs.com/install.sh | sh -- --preset standard

# From a local clone (development)
sh ./install.sh --preset advanced

# Without --preset (defaults to "standard" for new installs)
sh ./install.sh
```

## Available Presets

| Preset | Description | Features Enabled |
|---|---|---|
| **minimal** | Core only; all feature tiers are opt-in via Settings or YAML | Production features only |
| **standard** | Recommended for most users (default) | Production + Stable features |
| **advanced** | For explorers and power users | Production + Stable + Beta features (Alpha remains opt-in) |

## Preset Selection Wizard (Interactive Mode)

When running `./install.sh` interactively with a TTY (not piped), you can also configure the preset through the **setup wizard** after installation:

```bash
corvin-install
```

The wizard will show a menu to select the preset, or you can use the CLI directly:

```bash
corvin-preset-setup [minimal|standard|advanced]
```

## Configuration

The preset is stored in `~/.corvin/tenants/_default/tenant.corvin.yaml`:

```yaml
spec:
  preset: standard  # or "minimal" / "advanced"
  # ... other configuration fields ...
```

### Changing Presets After Installation

```bash
# Change to advanced
corvin-preset-setup advanced

# Verify
cat ~/.corvin/tenants/_default/tenant.corvin.yaml

# Restart CorvinOS for changes to take effect
killall corvinos-serve
corvinos-serve &
```

## Examples

### Fresh Installation (Standard Preset)
```bash
curl -fsSL https://corvin-labs.com/install.sh | sh
```

### Development Installation (Advanced Preset)
```bash
sh ./install.sh --preset advanced
```

### CI/Automation (Minimal Preset, No Interactive UI)
```bash
sh ./install.sh --no-hermes --preset minimal --autostart
```

## Feature Tier Progression

The four-tier system governs automatic feature promotion based on stability metrics:

- **Alpha** → tested, but early/experimental
- **Beta** → stability proven, adopted by beta testers
- **Stable** → low error rates, >5% adoption
- **Production** → high confidence, >25% adoption, <0.1% error rate

See `docs/FEATURE_TIERS.md` for more details on the metrics-driven promotion system (ADR-0286/0287/0288).
