"""Browser tool schemas (ADR-0182 Pillar B).

The canonical description of the ``browser.*`` action surface. The console REST
routes implement these; an MCP stdio bridge (or the Tool Execution Broker) can
register the same list verbatim so a WorkerEngine calls them as tools. Keeping
the schema in one place stops the REST layer and any future MCP bridge from
drifting apart.
"""
from __future__ import annotations

BROWSER_TOOLS: list[dict] = [
    {
        "name": "browser.navigate",
        "description": "Open a URL (http/https only). Egress-gated by the tenant "
                       "allowlist. Returns the Set-of-Marks observation of the loaded page.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser.observe",
        "description": "Re-scan the current page and return the numbered list of "
                       "interactive elements (Set-of-Marks). Call this after any page change.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser.click",
        "description": "Click the element with the given mark index. Sensitive "
                       "clicks (buy/send/delete/login) require user confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    },
    {
        "name": "browser.fill",
        "description": "Type text into the field with the given mark index. The "
                       "value is never logged or audited.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["index", "text"],
        },
    },
    {
        "name": "browser.fill_secret",
        "description": "Type a secret resolved from the vault by key name into the "
                       "field. The value never enters the model context or any log.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "vault_key": {"type": "string"}},
            "required": ["index", "vault_key"],
        },
    },
    {
        "name": "browser.read",
        "description": "Read visible text — of one element (by index) or the whole "
                       "page body (index omitted). Bounded length.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
        },
    },
    {
        "name": "browser.scroll",
        "description": "Scroll the page: down | up | top | bottom.",
        "inputSchema": {
            "type": "object",
            "properties": {"direction": {"type": "string",
                                         "enum": ["down", "up", "top", "bottom"]}},
        },
    },
    {
        "name": "browser.back",
        "description": "Go back one page in history and return the new observation.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser.screenshot",
        "description": "Return a JPEG screenshot (base64 data URL) of the current "
                       "viewport with the mark overlay painted on.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── ADR-0183 S2: expanded action surface ─────────────────────────────────
    {
        "name": "browser.hover",
        "description": "Hover the element with the given mark index (e.g. to reveal "
                       "a hover-only menu) without clicking it.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    },
    {
        "name": "browser.key",
        "description": "Press a single named key on the page: Enter (submit a form/"
                       "search), Tab, Escape, Backspace, Delete, Space, Arrow*, "
                       "Home/End, PageUp/PageDown, F1–F12. A committing key "
                       "(Enter/Space) on a sensitive form requires user confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "browser.select_option",
        "description": "Choose an option (by its value attribute) in the <select> "
                       "with the given mark index. The chosen value is never logged.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "value": {"type": "string"}},
            "required": ["index", "value"],
        },
    },
    {
        "name": "browser.upload_file",
        "description": "Attach a file to the file-input with the given mark index. The "
                       "file must already exist under the session's uploads directory "
                       "(no arbitrary host path); the filename only, never content, is logged.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "filename": {"type": "string"}},
            "required": ["index", "filename"],
        },
    },
    {
        "name": "browser.drag",
        "description": "Drag the element at from_index onto the element at to_index "
                       "(e.g. a slider or reorder handle).",
        "inputSchema": {
            "type": "object",
            "properties": {"from_index": {"type": "integer"},
                           "to_index": {"type": "integer"}},
            "required": ["from_index", "to_index"],
        },
    },
    {
        "name": "browser.tabs",
        "description": "List every open tab in this session (index, url, title) — "
                       "including a tab opened by a target=_blank click or window.open.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser.switch_tab",
        "description": "Make the tab with the given index (from browser.tabs) the "
                       "active page and return its Set-of-Marks observation.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    },
    {
        "name": "browser.extract_table",
        "description": "Parse the table (or table-role container) at the given mark "
                       "index into {headers, rows} JSON. Bounded row count.",
        "inputSchema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    },
    {
        "name": "browser.extract_form_schema",
        "description": "Describe every <form> on the current top-level document "
                       "(action/method + field name/type/required/label). Never "
                       "includes any field's current value.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

BROWSER_TOOL_NAMES = [t["name"] for t in BROWSER_TOOLS]
