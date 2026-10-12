# Forge intake (ADR-2254, CONCEPT-0106)

One fail-closed analysis stage for everything attached to a Forge. Slice 1: tabular files (csv, tsv, json, jsonl) on the **Tool Forge** only.

- **Code:** `core/forge_intake/intake.py` (stages), `core/console/corvin_console/routes/forge_intake_api.py` (routes), `attachment_ids` on `POST /forge-creator/tool/generate`.
- **Stages:** admit (no symlink, inside the upload area, size, archive by content) → sniff (Layer 24 plus an independent content check) → secret scan over every byte → projection (Layer 24 snapshot, PII detection, redaction under the operator's data policy) → the pre-spawn gate on the projection the engine will read.
- **Routes:** `POST /forge-intake/attachments` (one file, returns the report), `DELETE /forge-intake/attachments/{id}` (owner only). A generate request with `attachment_ids` re-analyses the stored bytes; unknown/foreign id 404, file changed since its report 409, refused item or attachments on a plugin 422 - the whole request fails.
- **What a generator sees:** only the bounded redacted pack (`PACK_MAX_BYTES` 24 KiB; it degrades sample → stats → columns and says so). The raw bytes stay in `<tenant>/global/forge_intake/uploads/att-<hex>/`.
- **Retention:** unused uploads 24 h, uploads a run consumed 14 d (pruned on every upload).
- **Audit:** `forge_intake.item_admitted | item_redacted | item_refused` - closed codes, counts and a hash prefix; never a file name or a value (`console_audit.forge_intake_item`).
- **Tests:** `tests/forge_intake/`, `core/console/tests/test_forge_intake_route_e2e.py`, `test_forge_intake_audit_e2e.py`, live spec `19-intake.spec.ts`.
- **Not built yet:** documents, connections, URLs, existing artifacts; Skill/Plugin/Layer attachments; resume/retry carrying attachments beyond the stored pack.
