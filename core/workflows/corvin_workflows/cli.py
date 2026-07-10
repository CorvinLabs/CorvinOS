"""CLI entry point: python -m corvin_workflows <subcommand> ...

Subcommands:
  list                                  list bundled + user workflows
  show <name>                           dump the resolved workflow YAML
  validate <name>                       run R1..R10 against the workflow
  run <name> [key=value ...]            execute the workflow (Stub-Engine MVP)

The slash-command layer (operator/bridges/shared/js/in_chat_commands.js)
shells out to this CLI with spawnSync — same pattern as /quota, /ldd-status,
/settings. Stdout is the user-facing reply; stderr carries diagnostics.

The MVP runs against the bundled StubEngine — every spawn returns canned
data, no LLM is hit. Real-engine wiring lands in a follow-up sub-phase.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engines import StubEngine, EngineCall
from .runner import DAGRunner, resume_workflow
from .storage import load_workflow
from .validator import WorkflowInvalid, validate

# ACS engine — optional; absent on lightweight installs.
try:
    _ACS_SHARED = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    if str(_ACS_SHARED) not in sys.path:
        sys.path.insert(0, str(_ACS_SHARED))
    from acs_engine_adapter import run_acs_workflow, list_acs_runs, get_acs_run  # type: ignore
    from acs_validator import validate_workflow_dict as _acs_validate_dict  # type: ignore
    _ACS_AVAILABLE = True
except ImportError:
    _ACS_AVAILABLE = False

_BUNDLED_DIR = Path(__file__).resolve().parent / "examples"


def _resolve_workflow_path(name: str) -> Path:
    """Look up a workflow by literal path, file stem, or `workflow.name` field.

    Order:
      1. literal path that exists on disk
      2. <bundled>/<name>.awp.yaml (file stem match)
      3. <bundled>/<name>            (full filename match)
      4. any bundled file whose `workflow.name:` equals `name`
    """
    p = Path(name)
    if p.exists():
        return p
    for variant in (f"{name}.awp.yaml", name):
        candidate = _BUNDLED_DIR / variant
        if candidate.exists():
            return candidate
    # Fall back to scanning the YAML's workflow.name field
    for f in _BUNDLED_DIR.glob("*.awp.yaml"):
        try:
            doc = load_workflow(f)
        except Exception:  # noqa: BLE001
            continue
        if doc.name == name:
            return f
    raise FileNotFoundError(
        f"workflow {name!r} not found "
        f"(searched {_BUNDLED_DIR} and the literal path {p})"
    )


def _list_workflows() -> int:
    print("Bundled workflows:")
    for f in sorted(_BUNDLED_DIR.glob("*.awp.yaml")):
        try:
            doc = load_workflow(f)
            print(f"  {doc.name:32s}  — {doc.description.strip().splitlines()[0][:80]}")
        except Exception as e:  # noqa: BLE001
            print(f"  {f.stem:32s}  (load error: {e})")
    return 0


def _show_workflow(name: str) -> int:
    p = _resolve_workflow_path(name)
    doc = load_workflow(p)
    print(f"# {p}")
    print(f"# engine:         {doc.engine}")
    print(f"# nodes:          {len(doc.graph)}")
    print(f"# awp version:    {doc.awp_version}")
    print(json.dumps(doc.raw, indent=2, sort_keys=True))
    return 0


def _validate_workflow(name: str) -> int:
    p = _resolve_workflow_path(name)
    doc = load_workflow(p)

    # ACS workflows (delegation_loop engine) use the full R1-R36 validator.
    if doc.engine == "delegation_loop":
        if not _ACS_AVAILABLE:
            print("ERROR: acs_validator not installed; cannot validate delegation_loop workflows.",
                  file=sys.stderr)
            return 1
        result = _acs_validate_dict(doc.raw)
        if not result.ok:
            for issue in result.errors:
                print(f"INVALID [{issue.rule_id}] {issue.message}", file=sys.stderr)
            return 1
        warnings = [i for i in result.issues if i.severity == "WARNING"]
        if warnings:
            for w in warnings:
                print(f"WARNING [{w.rule_id}] {w.message}", file=sys.stderr)
        print(f"OK: {doc.name} passes R1..R36 (engine=delegation_loop, acs)")
        return 0

    # DAG and other L26 engines: existing R1..R10 validator.
    try:
        validate(doc)
    except WorkflowInvalid as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print(f"OK: {doc.name} passes R1..R10 ({len(doc.graph)} nodes, engine={doc.engine})")
    return 0


def _parse_kv_inputs(raw: list[str]) -> dict[str, object]:
    """Turn ['ticker=NVDA', 'window_days=7'] into {ticker: 'NVDA', window_days: 7}.

    Integer-shaped values are coerced; everything else stays a string. JSON
    values are recognised by a leading '{' or '[' and parsed.
    """
    out: dict[str, object] = {}
    for tok in raw:
        if "=" not in tok:
            raise SystemExit(f"input arg {tok!r} missing '=' (use key=value)")
        k, _, v = tok.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            raise SystemExit(f"input arg {tok!r} has empty key")
        if v.startswith("{") or v.startswith("["):
            out[k] = json.loads(v)
        elif v.lstrip("-").isdigit():
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _build_default_stub() -> StubEngine:
    """The MVP demo engine. Returns canned but richly-structured data so the
    reporter can render a real markdown sentiment briefing. No LLM tokens
    spent — every value is deterministic.
    """
    FAKE_ARTICLES = [
        {
            "id": "A1",
            "source": "Reuters",
            "date": "2026-05-13",
            "title": "EPS beat: revenue up 38% YoY, guidance raised",
            "snippet": "Datacenter segment crossed $32B in the quarter; CEO highlighted "
                       "sovereign-AI contract pipeline.",
            "sentiment_hint": 0.92,
        },
        {
            "id": "A2",
            "source": "Bloomberg",
            "date": "2026-05-12",
            "title": "Analyst note: priced for perfection at 38x forward",
            "snippet": "Goldman: 'no margin of safety left in the multiple'; downgrade to neutral.",
            "sentiment_hint": 0.45,
        },
        {
            "id": "A3",
            "source": "Financial Times",
            "date": "2026-05-11",
            "title": "Supply chain rumors weigh on near-term outlook",
            "snippet": "TSMC capacity allocation reportedly tightened; competitors gain HBM access.",
            "sentiment_hint": 0.28,
        },
        {
            "id": "A4",
            "source": "WSJ",
            "date": "2026-05-10",
            "title": "Hyperscalers signal capex acceleration into H2",
            "snippet": "MSFT, GOOG, META each raised AI infrastructure budget targets at investor day.",
            "sentiment_hint": 0.81,
        },
        {
            "id": "A5",
            "source": "CNBC",
            "date": "2026-05-09",
            "title": "Insider sales: CFO offloads 80k shares on plan",
            "snippet": "Sales executed under 10b5-1; CFO still holds 410k post-trade.",
            "sentiment_hint": 0.42,
        },
    ]
    FAKE_POSTS = [
        {"id": "P1", "subreddit": "wallstreetbets", "upvotes": 8412,
         "text": "loaded the boat ahead of earnings, lfg 🚀", "sentiment_hint": 0.88},
        {"id": "P2", "subreddit": "wallstreetbets", "upvotes": 2310,
         "text": "weekly puts looking juicy at this multiple", "sentiment_hint": 0.18},
        {"id": "P3", "subreddit": "stocks", "upvotes": 1108,
         "text": "long-term thesis intact, FCF compounding faster than peers",
         "sentiment_hint": 0.75},
        {"id": "P4", "subreddit": "investing", "upvotes": 642,
         "text": "valuation makes me uneasy but I trust the moat", "sentiment_hint": 0.58},
        {"id": "P5", "subreddit": "wallstreetbets", "upvotes": 4901,
         "text": "guidance was insane, anyone selling here is going to regret it",
         "sentiment_hint": 0.90},
    ]

    def news_scorer(call: EngineCall) -> dict:
        arts = (call.state.get("news_fetcher") or {}).get("articles") or []
        scores = [a.get("sentiment_hint", 0.5) for a in arts]
        score = sum(scores) / max(len(arts), 1)
        # Spread of opinion within the news set (variance heuristic, normalised 0..1)
        if len(scores) >= 2:
            mean = sum(scores) / len(scores)
            var = sum((s - mean) ** 2 for s in scores) / len(scores)
            spread = min(1.0, var * 4)  # tuned so 0.25 var maps to 1.0
        else:
            spread = 0.0
        # Most-positive and most-negative headline
        sorted_arts = sorted(arts, key=lambda a: a.get("sentiment_hint", 0.5), reverse=True)
        return {
            "source": "news",
            "score": round(score, 3),
            "n": len(arts),
            "confidence": 0.82,
            "spread": round(spread, 3),
            "top_positive": sorted_arts[0] if sorted_arts else None,
            "top_negative": sorted_arts[-1] if sorted_arts else None,
            "by_outlet": {a["source"]: a["sentiment_hint"] for a in arts},
            "quotes": [
                f"{a['source']}: {a['title']}"
                for a in arts[:3]
            ],
        }

    def reddit_scorer(call: EngineCall) -> dict:
        ps = (call.state.get("reddit_fetcher") or {}).get("posts") or []
        # Upvote-weighted score — mirror what an actual sentiment pipeline would do
        total_w = sum(p.get("upvotes", 1) for p in ps) or 1
        score = sum(p.get("sentiment_hint", 0.5) * p.get("upvotes", 1) for p in ps) / total_w
        by_sub: dict[str, list[float]] = {}
        for p in ps:
            by_sub.setdefault(p["subreddit"], []).append(p.get("sentiment_hint", 0.5))
        sub_avg = {k: round(sum(v) / len(v), 3) for k, v in by_sub.items()}
        bull = sum(1 for p in ps if p.get("sentiment_hint", 0.5) >= 0.55)
        bear = sum(1 for p in ps if p.get("sentiment_hint", 0.5) <= 0.35)
        return {
            "source": "reddit",
            "score": round(score, 3),
            "n": len(ps),
            "confidence": 0.66,
            "bull_count": bull,
            "bear_count": bear,
            "by_subreddit": sub_avg,
            "top_posts": sorted(ps, key=lambda p: p.get("upvotes", 0), reverse=True)[:3],
            "quotes": [
                f"r/{p['subreddit']} (+{p['upvotes']}): {p['text']}"
                for p in sorted(ps, key=lambda p: p.get("upvotes", 0), reverse=True)[:3]
            ],
        }

    def contradiction(call: EngineCall) -> dict:
        iters = call.state.get("_iterations") or []
        by_source: dict[str, float] = {}
        for it in iters:
            for w in it.get("workers", []):
                if w.get("source") in ("news", "reddit"):
                    by_source[w["source"]] = w["score"]
        scores = list(by_source.values())
        spread = (max(scores) - min(scores)) if len(scores) >= 2 else 0.0
        if spread < 0.15:
            verdict = "aligned"
            note = "News and social agree closely — high-conviction signal."
        elif spread < 0.35:
            verdict = "consistent"
            note = "Minor divergence between channels but no contradiction."
        else:
            verdict = "contradictory"
            note = (
                "News skews more cautious than social; retail enthusiasm "
                "is not reflected in institutional coverage."
            )
        return {
            "kind": "contradiction_check",
            "spread": round(spread, 3),
            "verdict": verdict,
            "note": note,
            "by_source": {k: round(v, 3) for k, v in by_source.items()},
        }

    def manager(call: EngineCall) -> dict:
        if call.iteration == 1:
            return {"decision": "DELEGATE", "workers": [
                {"agent": "news_scorer", "instructions": "score news corpus"},
                {"agent": "reddit_scorer", "instructions": "upvote-weighted reddit score"}]}
        if call.iteration == 2:
            return {"decision": "DELEGATE", "workers": [
                {"agent": "contradiction_checker", "instructions": "news vs social cross-check"}]}
        iters = call.state.get("_iterations") or []
        news_w = reddit_w = contradiction_w = None
        for it in iters:
            for w in it.get("workers", []):
                if w.get("source") == "news":
                    news_w = w
                elif w.get("source") == "reddit":
                    reddit_w = w
                elif w.get("kind") == "contradiction_check":
                    contradiction_w = w
        scalar_scores = [
            x.get("score", 0.0) for x in (news_w, reddit_w) if x is not None
        ]
        # Weighted: news 0.6, reddit 0.4 (institutional > retail noise)
        if news_w and reddit_w:
            agg = round(0.6 * news_w["score"] + 0.4 * reddit_w["score"], 3)
        else:
            agg = round(sum(scalar_scores) / max(len(scalar_scores), 1), 3)
        # Aggregate confidence: floor at 0.55, cap at 0.95
        conf = 0.75
        if contradiction_w and contradiction_w.get("verdict") == "aligned":
            conf = 0.90
        elif contradiction_w and contradiction_w.get("verdict") == "contradictory":
            conf = 0.62
        return {
            "decision": "COMPLETE",
            "confidence": conf,
            "result": {
                "score": agg,
                "confidence": conf,
                "news": news_w,
                "reddit": reddit_w,
                "contradiction": contradiction_w,
            },
        }

    def reporter(call: EngineCall) -> dict:
        sa = call.state.get("sentiment_analysis") or {}
        score = sa.get("score", 0.0)
        conf = sa.get("confidence", 0.0)
        news = sa.get("news") or {}
        reddit = sa.get("reddit") or {}
        contra = sa.get("contradiction") or {}
        ticker = str(call.inputs.get("ticker", "?")).upper()
        window = call.inputs.get("window_days", "?")

        # ── Helpers ────────────────────────────────────────────────────
        def bar(v: float, width: int = 16) -> str:
            v = max(0.0, min(1.0, float(v)))
            filled = int(round(v * width))
            return "█" * filled + "░" * (width - filled)

        def label(v: float) -> str:
            if v >= 0.75:
                return "🟢 Strong Bullish"
            if v >= 0.60:
                return "🟢 Bullish"
            if v >= 0.45:
                return "🟡 Mixed"
            if v >= 0.30:
                return "🔴 Bearish"
            return "🔴 Strong Bearish"

        # ── Headline + score line ──────────────────────────────────────
        lines: list[str] = []
        lines.append(f"📊 **Sentiment Briefing — {ticker}**")
        lines.append(f"_Window: last {window} days · Confidence: {conf:.0%}_")
        lines.append("")
        lines.append(f"**Aggregate score:** `{score:.2f}` {bar(score)} {label(score)}")
        lines.append("")

        # ── Source breakdown ───────────────────────────────────────────
        lines.append("**Source Breakdown**")
        if news:
            lines.append(
                f"• News (n={news.get('n', 0)}): "
                f"`{news['score']:.2f}` {bar(news['score'], 12)}  "
                f"spread {news.get('spread', 0):.2f}"
            )
            for outlet, s in (news.get("by_outlet") or {}).items():
                lines.append(f"    └─ {outlet}: `{s:.2f}`")
        if reddit:
            lines.append(
                f"• Social (n={reddit.get('n', 0)}, upvote-weighted): "
                f"`{reddit['score']:.2f}` {bar(reddit['score'], 12)}  "
                f"bulls {reddit.get('bull_count', 0)} / bears {reddit.get('bear_count', 0)}"
            )
            for sub, s in (reddit.get("by_subreddit") or {}).items():
                lines.append(f"    └─ r/{sub}: `{s:.2f}`")
        lines.append("")

        # ── Cross-check verdict ───────────────────────────────────────
        if contra:
            verdict = contra.get("verdict", "?")
            icon = {"aligned": "✅", "consistent": "🟢", "contradictory": "⚠️"}.get(verdict, "•")
            lines.append(f"**Cross-Check:** {icon} {verdict.upper()} (spread {contra.get('spread', 0):.2f})")
            if contra.get("note"):
                lines.append(f"> {contra['note']}")
            lines.append("")

        # ── Notable headlines ─────────────────────────────────────────
        if news.get("top_positive") or news.get("top_negative"):
            lines.append("**Notable Headlines**")
            tp = news.get("top_positive")
            tn = news.get("top_negative")
            if tp:
                lines.append(
                    f"🟢 *{tp['source']}* ({tp['date']}) — {tp['title']}\n"
                    f"   {tp.get('snippet', '')}"
                )
            if tn and tn != tp:
                lines.append(
                    f"🔴 *{tn['source']}* ({tn['date']}) — {tn['title']}\n"
                    f"   {tn.get('snippet', '')}"
                )
            lines.append("")

        # ── Reddit pulse ──────────────────────────────────────────────
        if reddit.get("top_posts"):
            lines.append("**Reddit Pulse (top by upvotes)**")
            for p in reddit["top_posts"]:
                lines.append(f"• r/{p['subreddit']} (+{p['upvotes']}): {p['text']}")
            lines.append("")

        # ── Risk / watch-out heuristic ────────────────────────────────
        risks: list[str] = []
        if news.get("spread", 0) > 0.20:
            risks.append("News spread is wide — analyst opinions are diverging.")
        if reddit.get("bear_count", 0) >= 1 and reddit.get("bear_count", 0) >= reddit.get("n", 0) * 0.2:
            risks.append("Non-trivial bear contingent on social side.")
        if contra.get("verdict") == "contradictory":
            risks.append("News and social disagree — single-channel reads are unreliable.")
        if not risks:
            risks.append("No structural risk flags above threshold.")
        lines.append("**Watch-Outs**")
        for r in risks:
            lines.append(f"• {r}")
        lines.append("")

        lines.append(
            f"_Generated by corvin-workflows L26 · "
            f"engine=dag · delegation-iters=3 · workers=3_"
        )

        report_text = "\n".join(lines)
        return {
            "report_text": report_text,
            "score": score,
            "confidence": conf,
            "label": label(score),
        }

    def default(call: EngineCall) -> dict:
        dispatch = {
            "sentiment_manager": manager,
            "news_scorer": news_scorer,
            "reddit_scorer": reddit_scorer,
            "contradiction_checker": contradiction,
            "reporter": reporter,
        }
        fn = dispatch.get(call.agent)
        if fn is None:
            raise RuntimeError(f"no canned response for agent {call.agent!r}")
        return fn(call)

    return StubEngine(
        responses={
            "news_fetcher": {"articles": FAKE_ARTICLES},
            "reddit_fetcher": {"posts": FAKE_POSTS},
        },
        default=default,
    )


def _run_workflow_acs(
    name: str,
    raw_inputs: list[str],
    *,
    format_: str,
    dry_run: bool = False,
) -> int:
    """Dispatch a delegation_loop workflow to the ACS engine."""
    if not _ACS_AVAILABLE:
        print("ERROR: acs_runtime not installed; cannot run delegation_loop workflows.",
              file=sys.stderr)
        print("Install the ACS package or run from the CorvinOS repo root.", file=sys.stderr)
        return 1

    p = _resolve_workflow_path(name)
    doc = load_workflow(p)
    result_acs = _acs_validate_dict(doc.raw)
    if not result_acs.ok:
        for issue in result_acs.errors:
            print(f"INVALID [{issue.rule_id}] {issue.message}", file=sys.stderr)
        return 1

    inputs = _parse_kv_inputs(raw_inputs)
    out = run_acs_workflow(doc.raw, inputs=inputs, dry_run=dry_run)

    if format_ == "json":
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") == "success" else 1

    icon = "✅" if out.get("status") == "success" else "❌"
    lines: list[str] = [
        f"{icon} **{doc.name}** → `{out.get('status')}`  (engine=acs)",
        f"Run-ID: `{out.get('run_id')}`  ·  {out.get('duration_s', 0):.1f}s",
    ]
    if out.get("summary"):
        lines.append(f"Summary: {out['summary']}")
    if out.get("error"):
        lines.append(f"Error: {out['error']}")
    if out.get("artifacts"):
        lines.append(f"Artifacts: {json.dumps(out['artifacts'], default=str)}")
    if dry_run:
        lines.append("_(dry-run — no workers spawned)_")
    print("\n".join(lines))
    return 0 if out.get("status") == "success" else 1


def _build_engine(engine_name: str):
    if engine_name == "stub":
        return _build_default_stub()
    if engine_name == "claude":
        from .engines_claude import ClaudeCliEngine

        return ClaudeCliEngine()
    raise SystemExit(f"unknown --engine {engine_name!r} (expected 'stub' or 'claude')")


def _format_run_result(doc, result, inputs: dict, *, format_: str, engine) -> tuple[str, int]:
    """Shared pretty/json rendering for both a fresh run() and a resume()."""
    history_len = len(getattr(engine, "history", []))

    if format_ == "json":
        payload = {
            "workflow": result.workflow,
            "state": result.state,
            "run_id": result.run_id,
            "paused_at_node": result.paused_at_node,
            "paused_prompt": result.paused_prompt,
            "inputs": result.inputs,
            "error": result.error,
            "nodes": {
                nid: {
                    "type": n.node_type,
                    "status": n.status,
                    "wall_ms": int(n.wall_s * 1000),
                    "output": n.output,
                    "error": n.error,
                }
                for nid, n in result.nodes.items()
            },
            "final_state": result.final_state,
        }
        exit_code = 0 if result.state in ("complete", "paused") else 1
        return json.dumps(payload, indent=2, default=str), exit_code

    icon = {"complete": "✅", "paused": "⏸️", "failed": "❌"}.get(result.state, "❓")
    lines: list[str] = [f"{icon} **{doc.name}** → `{result.state}`"]
    if result.state == "paused":
        lines.append(f"Run-ID: `{result.run_id}`  (resume with: `resume {result.run_id} \"<reply>\"`)")
        lines.append(f"Waiting at node `{result.paused_at_node}`: {result.paused_prompt}")
    lines.append(f"Inputs: `{json.dumps(inputs, sort_keys=True)}`")
    lines.append(f"Nodes: {len(result.nodes)} · Engine spawns: {history_len}")
    lines.append("")
    for nid, n in result.nodes.items():
        status_tag = "" if n.status == "success" else f" [{n.status}]"
        if n.node_type == "delegation_loop":
            term = n.output.get("terminal", {})
            iters = n.output.get("iterations", [])
            lines.append(
                f"• `{nid}` ({n.node_type}){status_tag}  "
                f"→ {term.get('state')}  "
                f"iter={term.get('iteration')}  "
                f"workers={n.output.get('workers_spawned')}"
            )
            for it in iters:
                dec = it["manager"]["decision"]
                wc = len(it["workers"])
                lines.append(f"    iter {it['iteration']}: {dec} ({wc} workers)")
        else:
            lines.append(f"• `{nid}` ({n.node_type}){status_tag}  {int(n.wall_s*1000)}ms")
    reporter_node = result.nodes.get("reporter")
    if reporter_node and reporter_node.output.get("report_text"):
        lines.append("")
        lines.append("```")
        lines.append(reporter_node.output["report_text"])
        lines.append("```")
    exit_code = 0 if result.state in ("complete", "paused") else 1
    return "\n".join(lines), exit_code


def _run_workflow(
    name: str, raw_inputs: list[str], *, format_: str, dry_run: bool = False, engine_name: str = "stub",
) -> int:
    p = _resolve_workflow_path(name)
    doc = load_workflow(p)

    # Route delegation_loop workflows to the ACS engine.
    if doc.engine == "delegation_loop":
        return _run_workflow_acs(name, raw_inputs, format_=format_, dry_run=dry_run)

    try:
        validate(doc)
    except WorkflowInvalid as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1

    if dry_run:
        print(f"✅ **{doc.name}** validated (dry-run, engine={doc.engine})")
        return 0

    inputs = _parse_kv_inputs(raw_inputs)
    engine = _build_engine(engine_name)
    runner = DAGRunner(doc, engine=engine)
    result = runner.run(inputs=inputs)

    text, exit_code = _format_run_result(doc, result, inputs, format_=format_, engine=engine)
    print(text)
    return exit_code


def _resume_workflow_cmd(run_id: str, reply: str, *, format_: str, engine_name: str = "stub") -> int:
    engine = _build_engine(engine_name)
    try:
        result = resume_workflow(run_id, reply, engine=engine)
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    # doc.name isn't available post-resume without reloading; result.workflow carries it.
    class _DocStub:
        name = result.workflow
    text, exit_code = _format_run_result(_DocStub(), result, result.inputs, format_=format_, engine=engine)
    print(text)
    return exit_code


def _schedule_workflow(
    *,
    when: str,
    name: str,
    channel: str,
    chat_id: str,
    sender: str,
    inputs: list[str],
) -> int:
    """Register a recurring/one-shot workflow run via the shared scheduler.

    Imports `scheduler` lazily so that running the workflow CLI from outside
    the bridge environment (tests, ad-hoc inspection) still works when the
    voice plugin tree is absent.
    """
    try:
        _scheduler_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice" / "bridges" / "shared"
        )
        sys.path.insert(0, str(_scheduler_dir))
        import scheduler  # type: ignore
    except ImportError as e:
        print(f"⚠ scheduler module not reachable: {e}", file=sys.stderr)
        return 1
    # Validate the workflow exists before scheduling (loud-failure-now beats
    # silent-failure-at-fire-time).
    try:
        p = _resolve_workflow_path(name)
    except FileNotFoundError as e:
        print(f"⚠ {e}", file=sys.stderr)
        return 1
    doc = load_workflow(p)
    try:
        validate(doc)
    except WorkflowInvalid as e:
        print(f"⚠ workflow invalid before scheduling: {e}", file=sys.stderr)
        return 1
    workflow_inputs = _parse_kv_inputs(inputs)
    try:
        item = scheduler.add_task(
            channel=channel,
            chat_id=chat_id,
            sender=sender,
            text=f"[workflow] {name}",  # human-readable label for `/schedule list`
            when=when,
            kind="workflow",
            workflow_name=name,
            workflow_inputs=workflow_inputs,
        )
    except ValueError as e:
        print(f"⚠ {e}", file=sys.stderr)
        return 1
    print(f"⏰ Scheduled `{name}` (task `{item['id']}`)")
    print(f"   when:   {when}")
    print(f"   cron:   {item.get('cron') or '(one-shot)'}")
    print(f"   next:   {scheduler.humanize(item)}")
    print(f"   inputs: {json.dumps(workflow_inputs, sort_keys=True)}")
    return 0


def _schedule_list(*, channel: str | None, chat_id: str | None) -> int:
    try:
        _scheduler_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice" / "bridges" / "shared"
        )
        sys.path.insert(0, str(_scheduler_dir))
        import scheduler  # type: ignore
    except ImportError as e:
        print(f"⚠ scheduler module not reachable: {e}", file=sys.stderr)
        return 1
    items = scheduler.list_tasks(channel=channel, chat_id=chat_id)
    items = [i for i in items if i.get("kind") == "workflow"]
    if not items:
        print("No scheduled workflows.")
        return 0
    print(f"{len(items)} scheduled workflow run(s):")
    for it in sorted(items, key=lambda i: i.get("next_run", 0)):
        wf = it.get("workflow_name", "?")
        inputs = it.get("workflow_inputs", {})
        line = scheduler.humanize(it)
        print(f"  {line}  → {wf} {json.dumps(inputs, sort_keys=True)}")
    return 0


def _schedule_rm(task_id: str) -> int:
    try:
        _scheduler_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice" / "bridges" / "shared"
        )
        sys.path.insert(0, str(_scheduler_dir))
        import scheduler  # type: ignore
    except ImportError as e:
        print(f"⚠ scheduler module not reachable: {e}", file=sys.stderr)
        return 1
    if scheduler.remove_task(task_id):
        print(f"Removed task {task_id}.")
        return 0
    print(f"No task with id {task_id}.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="corvin_workflows",
        description="Corvin L26 — AWP workflow CLI",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list bundled workflows")
    sp = sub.add_parser("show", help="dump the resolved YAML")
    sp.add_argument("name")
    sp = sub.add_parser("validate", help="run R1..R10 validation")
    sp.add_argument("name")
    sp = sub.add_parser("run", help="execute a workflow (dag→Stub / delegation_loop→ACS)")
    sp.add_argument("name")
    sp.add_argument("inputs", nargs="*", help="key=value pairs")
    sp.add_argument(
        "--format",
        dest="fmt",
        choices=("pretty", "json"),
        default="pretty",
    )
    sp.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="validate only; do not spawn any workers or LLM calls",
    )
    sp.add_argument(
        "--engine",
        dest="engine_name",
        choices=("stub", "claude"),
        default="stub",
        help="'stub' (canned/no tokens, default) or 'claude' (real headless `claude -p` calls)",
    )
    sp = sub.add_parser("resume", help="continue a workflow paused at an ask_human node")
    sp.add_argument("run_id")
    sp.add_argument("reply", help="the human's reply text")
    sp.add_argument("--format", dest="fmt", choices=("pretty", "json"), default="pretty")
    sp.add_argument("--engine", dest="engine_name", choices=("stub", "claude"), default="stub")
    # `schedule` sub-command tree: add / list / rm
    sp = sub.add_parser(
        "schedule",
        help="cron-driven workflow runs (writes into channel outbox at fire time)",
    )
    sched_sub = sp.add_subparsers(dest="sched_cmd", required=True)
    sp_add = sched_sub.add_parser("add", help="add a scheduled workflow run")
    sp_add.add_argument("when", help='cron 5-field (e.g. "0 9 * * *") or "in 5m" / ISO 8601')
    sp_add.add_argument("name", help="workflow name (e.g. news_sentiment_research)")
    sp_add.add_argument("inputs", nargs="*", help="key=value pairs")
    sp_add.add_argument("--channel", required=True,
                        help="bridge channel (discord/telegram/slack/whatsapp)")
    sp_add.add_argument("--chat", required=True, dest="chat_id",
                        help="chat id (the channel/server identifier)")
    sp_add.add_argument("--sender", default="scheduler",
                        help="display sender (defaults to 'scheduler')")
    sp_list = sched_sub.add_parser("list", help="list scheduled workflow runs")
    sp_list.add_argument("--channel", default=None)
    sp_list.add_argument("--chat", default=None, dest="chat_id")
    sp_rm = sched_sub.add_parser("rm", help="remove a scheduled task by id")
    sp_rm.add_argument("task_id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return _list_workflows()
    if args.cmd == "show":
        return _show_workflow(args.name)
    if args.cmd == "validate":
        return _validate_workflow(args.name)
    if args.cmd == "run":
        return _run_workflow(args.name, args.inputs,
                             format_=args.fmt, dry_run=getattr(args, "dry_run", False),
                             engine_name=getattr(args, "engine_name", "stub"))
    if args.cmd == "resume":
        return _resume_workflow_cmd(args.run_id, args.reply, format_=args.fmt,
                                     engine_name=getattr(args, "engine_name", "stub"))
    if args.cmd == "schedule":
        if args.sched_cmd == "add":
            return _schedule_workflow(
                when=args.when, name=args.name,
                channel=args.channel, chat_id=args.chat_id,
                sender=args.sender, inputs=args.inputs,
            )
        if args.sched_cmd == "list":
            return _schedule_list(channel=args.channel, chat_id=args.chat_id)
        if args.sched_cmd == "rm":
            return _schedule_rm(args.task_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
