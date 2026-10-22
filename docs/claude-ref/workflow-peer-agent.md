# Workflow node `peer_agent`

A DAG node that hands one step to an agent of a **paired peer installation** - another organisation's
CorvinOS. The decision record is ADR-2276 in Corvin-Knowledge (Appendix A: the data-grade rule), the plan is
PLAN-0963. It reuses the federation transport unchanged (`core/federation/delegation.py::delegate`: signed
TaskEnvelope, instance pin, audit-first, delegation ledger); see [layer-38-a2a-network.md](layer-38-a2a-network.md)
("Agent federation"). No new wire op, no new protocol field.

Diagram: `docs/diagrams/workflow-peer-agent-flow.svg`.

## Configuration

```yaml
- id: legal_review
  type: peer_agent
  depends_on: [draft]
  endpoint_id: ep_acme          # pinned (recommended) ...
  agent_id: contract_reviewer   # ... together with agent_id
  # capability: analysis        # or: best FRESH peer agent for the capability
  instruction_from: draft.summary   # or: instruction: "literal text"
  timeout_s: 300                # 1..900, default 300
  retry: {error_strategy: fail_branch}   # max_retries is ignored: never retried
```

Validator (`node_types._validate_peer_agent`, rule R10): exactly one addressing mode (`endpoint_id`+`agent_id`
or `capability`), ids of 1-128 characters `A-Z a-z 0-9 - _ .`, `capability` one of the federation capabilities,
exactly one of `instruction` / `instruction_from` (a selector, so R9b applies), `timeout_s` 1..900, **unknown
keys rejected** - in particular the tenant, run and task id can never come from the node.

## What happens at run time (`_execute_peer_agent`)

1. The instruction is resolved. More than 16 KiB is refused (`instruction_too_large`), never truncated.
2. **Exactly once.** `task_id = sha256(run_id + ":" + node_id)[:32]`. The runner passes the tenant, the run id and
   the workflow id in the run state (`__tenant_id__`, `__run_id__`, `__workflow__`); a resumed run keeps its id.
   If the delegation ledger already holds a task with that id whose status is not `rejected`, the node fails with
   `outcome_unknown` (audit `workflow.peer_outcome_unknown`) and **sends nothing**: it may have run at the peer.
   The operator decides (re-run the workflow). Only an explicit `rejected` (the peer refused before running
   anything) may be asked again.
3. **Outbound gate** (`peer_gate.evaluate_peer_outbound`, fail-closed, on the exact payload):
   L44 acceptable use -> data grade -> endpoint pairing. A refusal is audited `workflow.peer_refused` with a closed
   reason code and never carries text.
4. **Audit-first.** `workflow.peer_delegated` is written to the tenant chain, then `delegate()` writes
   `federation.task_delegated`; if either cannot be written, nothing is sent.
5. The answer is mapped: `status`, `ok`, `result`, `untrusted: true`, `peer_instance_id`, `agent_address`,
   `task_id`, `our_chain_tail`, `peer_chain_tail`. A non-ok result raises with a closed reason (so `fail_branch`
   applies); `workflow.peer_completed` is written either way.

`peer_agent` is in `_NON_RETRYABLE_TYPES` and is engine-free (`node_requires_engine` is false).

## The outbound gate

| Check | Refuses with | Notes |
|---|---|---|
| L44 house rules (`spawn_gates.check_l44`, channel `workflow-peer`) | `l44_refused` | mandatory, fail-closed by contract |
| Data grade of the payload (`data_classification.classify_task`) | `classified_above_public` | `PUBLIC` may go. Above it only if the tenant policy releases THAT endpoint. `SECRET` (credential patterns) is never released |
| Endpoint registry entry, enabled, with an instance pin | `endpoint_not_paired` / `endpoint_not_pinned` | |
| Any gate error (module missing, classifier error, unreadable policy) | `gate_unavailable` | |

The L34 engine matrix is **not** used: a peer is not an engine of this installation, and `check_l34` is fail-open
without a tenant policy. Release is an operator decision in the co-owned, mode-0600 tenant YAML:

```yaml
spec:
  workflows:
    peer_policy:
      endpoints:
        ep_acme: {max_classification: internal}   # internal | confidential
```

The file is read on every send. No entry, a malformed entry or `secret` releases nothing. There is no environment
switch. (A console editor for this policy does not exist yet.) The peer organisation's GDPR role is not decided
here: until the operator releases an endpoint, classified data does not leave.

With `capability` the endpoint is ranked among FRESH peer agents (catalog TTL 300 s, `include_local=False`) and the
gate runs on the chosen endpoint; a refusal does not fall through to the next candidate.

## The peer's answer is foreign text

It is capped at 16 KiB, passed through the sanitiser every inbound A2A instruction passes
(`a2a_worker.sanitize_instruction`: NFKC, control characters, framing-escape rejection -> `peer_result_rejected`),
flagged `untrusted: true`, and stored as data. Selectors can read it; nothing parses it as workflow YAML or node
config. A later `agent` node that receives it in a prompt goes through the pre-spawn gates as usual.

## Audit events (scalars only, no text)

`workflow.peer_delegated` (INFO), `workflow.peer_completed` (INFO), `workflow.peer_refused` (WARNING),
`workflow.peer_outcome_unknown` (WARNING); fields `tenant_id, wid, rid, node_id, endpoint_id, peer_instance_id,
agent_id, status, duration_ms, data_class, reason_code`. Registered in `EVENT_SEVERITY` and `_EVENT_ALLOWLIST`
(`corvin_operator/forge/forge/security_events.py`). Both installations cite the hop by hash: the result carries
`our_chain_tail` (A's tail sent in the envelope) and `peer_chain_tail` (B's, signed in the response).

## Console

- `routes/workflow_dag_run.py::DAG_ONLY_TYPES` includes `peer_agent`: a console run uses the shared `DAGRunner`
  (and passes the workflow id), never the generic per-node executor.
- The synthetic readiness check ([workflow-readiness.md](workflow-readiness.md)) stubs the node: config validated,
  a pinned endpoint must be paired, a canned answer, **nothing is sent**.
- UI: palette entry "Peer agent" and a peer picker in the node inspector (`PeerAgentPicker`, data from
  `/federation/peers` and `/federation/peer-agents`). With no paired peer the palette adds nothing and says why; a
  stale catalog is named and can be refreshed. The designer chat is not taught this node on purpose.

## Tests

`core/workflows/tests/test_peer_agent_node.py` (validator, executor, exactly-once, foreign output, readiness stub),
`test_peer_gate.py`, `tests/security/test_workflow_peer_audit_events.py`,
`core/console/tests/test_workflow_peer_agent_console_e2e.py`, and the two-instance run
`tests/federation/test_workflow_peer_agent_e2e.py` (real HTTP and signatures, B's receiver writes a real chain,
both chains verified). Frontend: `web-next/tests/unit/peer-agent-picker.test.tsx`,
`web-next/tests/e2e/workflow-peer-agent.spec.ts`.

## Not in v1

Asynchronous peer results (a `awaiting_peer` pause), choosing a peer without the policy, an organisation label on
endpoints, answer attachments, retries.
