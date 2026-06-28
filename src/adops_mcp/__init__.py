"""adops-mcp: an MCP server for managing native-ad campaigns in plain English.

The MCP client (Claude) is the natural-language layer: it turns a media buyer's
sentence into a typed :class:`~adops_mcp.rules.schema.RuleSet` and calls this
server's tools. The server does the deterministic part — validate the ruleset,
evaluate it against live campaign data, build an auditable dry-run plan, gate
execution behind a confirm token, execute, and audit.
"""

__version__ = "0.1.0"
