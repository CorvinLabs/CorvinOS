"""E2E: PostgreSQL plugin idea, all the way through the pipeline (ADR-0253).

This is the flagship scenario from ADR-0253's Testing Strategy table
("PostgreSQL example: interview → scaffold → implement → E2E tests pass") and
doubles as the demo the Plugin-Builder ships with. `data_connector` was chosen
deliberately, not for convenience: it is one of the two plugin types with NO
official template in `core/plugins/templates/` (`corvin_plugins.surface_map`),
so this exercises the Builder-owned generic-provider fallback path — the path
that did not exist anywhere in the codebase before this feature.
"""
from __future__ import annotations

import re

import pytest

from plugin_builder.generators import write_artifacts
from plugin_builder.interview import InterviewSession
from plugin_builder.models import PluginKind

POSTGRES_ANSWERS = [
    "Postgres Connector",
    "We need to query a PostgreSQL database from a CorvinOS turn without hand-rolled SQL glue code.",
    "Data analysts and backend developers who already run Postgres",
    "none",
    "MVP first, expand later",
    "psycopg, sqlalchemy",
    "yes",
    "yes",
    "db.internal.example.com",
    "none",
    "mvp",
    "read-only queries only for the MVP",
]


@pytest.fixture
def confirmed_session() -> InterviewSession:
    s = InterviewSession(session_id="e2e-postgres")
    for answer in POSTGRES_ANSWERS:
        s.answer(answer)
    reply = s.answer("confirm")
    assert "writing artifacts" in reply.lower()
    return s


def test_interview_classifies_as_data_connector_provider(confirmed_session):
    idea, classification = confirmed_session.result()
    assert idea.plugin_name == "Postgres Connector"
    assert classification.kind == PluginKind.PROVIDER
    assert classification.plugin_type == "data_connector"
    assert classification.confidence > 0.5


def test_scaffold_writes_generic_provider_template(confirmed_session, tmp_path):
    idea, classification = confirmed_session.result()
    result = write_artifacts(idea, classification, tmp_path)

    assert result.dest.is_dir()
    assert result.plugin_id == "community.postgres-connector"
    scaffold_names = {p.name for p in result.scaffold_files}
    assert scaffold_names == {"plugin.py"}
    doc_names = {p.name for p in result.doc_files}
    assert doc_names == {
        "plugin-idea.md", "plugin-architecture.md", "build-plan.md",
        "plugin-adr-community-postgres-connector.md",
    }
    # Both official-template warnings from the classifier should have made it
    # through as scaffold warnings too, OR be visible in the idea doc — either
    # way an author must not be able to miss "nothing calls this type".
    idea_doc = (result.dest / "docs" / "plugin-idea.md").read_text()
    assert "NOTHING currently invokes it" in idea_doc


def test_generated_plugin_satisfies_corvin_plugin_protocol(confirmed_session, tmp_path):
    """The scaffold isn't just text — it is a structurally valid CorvinPlugin."""
    pytest.importorskip("corvin_plugins")
    from corvin_plugins.protocol import CorvinPlugin, HealthStatus, PluginContext

    idea, classification = confirmed_session.result()
    result = write_artifacts(idea, classification, tmp_path)
    plugin_py = result.dest / "plugin.py"
    src = plugin_py.read_text()

    # No leftover placeholder tokens — every __TOKEN__ must have been substituted.
    assert not re.search(r"__[A-Z_]+__", src)

    namespace: dict = {}
    exec(compile(src, str(plugin_py), "exec"), namespace)  # noqa: S102 — trusted, self-generated fixture code
    cls = namespace["MyProviderPlugin"]
    assert cls.plugin_id == "community.postgres-connector"
    assert cls.plugin_type == "data_connector"
    instance = cls()
    assert isinstance(instance, CorvinPlugin)
    health = instance.health_check()
    assert isinstance(health, HealthStatus)
    assert health.ok

    ctx = PluginContext(
        plugin_id=cls.plugin_id,
        tenant_id="_default",
        corvin_home=tmp_path,
        config={},
        audit_emit=lambda *a, **k: None,
    )
    instance.on_load(ctx)  # must not raise even though data_connector_registry is None


def test_second_run_same_idea_refuses_to_overwrite(confirmed_session, tmp_path):
    idea, classification = confirmed_session.result()
    write_artifacts(idea, classification, tmp_path)
    with pytest.raises(FileExistsError):
        write_artifacts(idea, classification, tmp_path)


def test_docs_reference_each_other_consistently(confirmed_session, tmp_path):
    idea, classification = confirmed_session.result()
    result = write_artifacts(idea, classification, tmp_path)
    idea_doc = (result.dest / "docs" / "plugin-idea.md").read_text()
    plan_doc = (result.dest / "docs" / "build-plan.md").read_text()
    adr_doc = next((result.dest / "docs").glob("plugin-adr-*.md")).read_text()

    assert "Postgres Connector" in idea_doc
    assert "corvin plugin check" in plan_doc
    assert "data_connector" in adr_doc
    assert "PROPOSED" in adr_doc
