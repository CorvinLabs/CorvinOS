# Operator Workflows

Place your AWP workflow files here as `<name>.awp.yaml`.

Each workflow is installed to `<corvin_home>/tenants/_default/workflows/`
when `corvin pkg install` runs.

## Format

See ADR-0011 (workflow plugins) and `docs/claude-ref/awpkg.md` for the
full `.awp.yaml` schema. Quick template:

```yaml
awp: "1.0"
id: "com.yourname.my-workflow"
name: "My Workflow"

triggers:
  slash:
    command: "/my-workflow"
    description: "Run my workflow"

agents:
  - id: main
    persona: coder
    prompt: |
      Do the thing.

dag:
  - id: step1
    agent: main
```

## Examples

See `core/workflows/corvin_workflows/examples/` in the repo
for reference workflows.
