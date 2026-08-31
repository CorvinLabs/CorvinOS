# systemd-user units for the compute worker

The compute worker is a long-running per-tenant daemon. systemd-user
units make restart-on-crash an operator-managed concern (rather than
the plugin shipping its own supervisor).

## One-time install

```bash
mkdir -p ~/.config/systemd/user
cp core/compute/systemd/corvin-compute@.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
```

The template unit reads the tenant id from the systemd instance suffix
(`%i`). Adjust the `ExecStart` path inside the unit file if your repo
checkout lives somewhere other than `~/projects/corvinOS/`.

## Enable + start per tenant

```bash
# Replace _default with your tenant id (e.g. acme).
systemctl --user enable --now corvin-compute@_default.service

# Logs
journalctl --user -u corvin-compute@_default.service -f
```

## Status

```bash
systemctl --user status corvin-compute@_default.service
```

## Stop / disable

```bash
systemctl --user disable --now corvin-compute@_default.service
```

## Recovery semantics

When the unit restarts after a crash, the worker scans
`<corvin_home>/tenants/<tid>/compute/runs/` and picks up any run
whose `summary.json::state` is non-terminal. Iteration files are
append-only, so re-running `strategy.update(history, history)` is a
no-op for the bundled strategies (grid + random are stateless; the
Bayesian strategy re-fits the GP from history on every batch).

Runs that fail recovery (e.g. the strategy is no longer installed)
are marked `state=failed`, `convergence_reason=recovery-failed:...`
and a `compute.run_failed` event is emitted.
