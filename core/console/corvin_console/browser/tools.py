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
]

BROWSER_TOOL_NAMES = [t["name"] for t in BROWSER_TOOLS]
