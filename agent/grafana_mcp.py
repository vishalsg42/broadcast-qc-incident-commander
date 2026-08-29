"""The official Grafana MCP server, filtered down to a read-only surface.

Two things this buys that a hand-rolled client does not.

The agent gets a REAL toolbox rather than four fixed phases: it decides which
query to run next, and can go somewhere the four-phase sequence never went.

And the read-only boundary stops being a code-review promise. The server ships
74 tools, several of which write - `create_incident`, `create_annotation`,
`update_dashboard`. The agent is handed an ALLOWLIST of four, so a write tool is
not something it is asked not to call; it is something it cannot see.

Defence in depth, in the order it applies:

  1. `--disable-*` flags        the server never registers those categories
  2. `tool_filter`              ADK exposes only the named tools to the model
  3. a separate credential      the write path lives in agent/annotations.py
                                with its own token, and is never on this session

The write path stays exactly where it was. This module is retrieval only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where the deploy puts it, then anything on PATH, then a local checkout - so a
# developer can run the agentic path without replicating the container layout.
BINARY_CANDIDATES = (
    "/usr/local/bin/mcp-grafana",
    "mcp-grafana",
    str(ROOT / "bin" / "mcp-grafana"),
)

# The agent's entire view of Grafana. Everything else the server offers -
# including every write tool - is simply absent from its toolbox.
READ_ONLY_TOOLS = [
    "list_datasources",
    "query_loki_logs",
    # Discovery. Without these the model guesses label names, and a guessed tool
    # name aborts the whole ADK invocation rather than coming back as an error
    # it could recover from - so the tools it naturally reaches for are present.
    "list_loki_label_names",
    "list_loki_label_values",
    "tempo_traceql-search",
    "tempo_get-trace",
]

# Categories the server should not even register. Redundant with the allowlist
# on purpose: if the allowlist is ever widened carelessly, these still hold.
DISABLED_CATEGORIES = [
    "--disable-admin",
    "--disable-provisioning",
    "--disable-snapshot",
    "--disable-alerting",
    "--disable-oncall",
    "--disable-rendering",
]


class GrafanaMcpUnavailable(RuntimeError):
    """The MCP server binary is not present, so the agentic path cannot run."""


def binary_path() -> str:
    """Locate the mcp-grafana binary, or say clearly that it is missing."""
    for candidate in BINARY_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    raise GrafanaMcpUnavailable(
        "mcp-grafana not found. The agentic reasoner needs the Grafana MCP "
        f"server on one of: {', '.join(BINARY_CANDIDATES)}. "
        "scripts/fetch_mcp_grafana.sh installs it."
    )


def server_env() -> dict[str, str]:
    """Only what the server needs. Not the parent process's whole environment.

    The parent holds OTLP credentials and Vertex configuration that the Grafana
    server has no business seeing, and a subprocess inheriting everything is how
    a secret ends up somewhere nobody audited.
    """
    url = os.environ.get("GRAFANA_URL")
    token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN") or os.environ.get("GRAFANA_TOKEN")
    if not url or not token:
        raise GrafanaMcpUnavailable(
            "GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN must be set for the "
            "Grafana MCP server"
        )
    return {
        "GRAFANA_URL": url,
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": token,
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
    }


def build_toolset(*, timeout_s: float = 30.0):
    """An McpToolset over stdio, restricted to the read-only allowlist."""
    # Imported here rather than at module scope: ADK's MCP support is an optional
    # extra, and the scripted path must keep working without it installed.
    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from mcp import StdioServerParameters

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=binary_path(),
                args=list(DISABLED_CATEGORIES),
                env=server_env(),
            ),
            timeout=timeout_s,
        ),
        tool_filter=list(READ_ONLY_TOOLS),
    )


__all__ = [
    "DISABLED_CATEGORIES",
    "READ_ONLY_TOOLS",
    "GrafanaMcpUnavailable",
    "binary_path",
    "build_toolset",
    "server_env",
]
