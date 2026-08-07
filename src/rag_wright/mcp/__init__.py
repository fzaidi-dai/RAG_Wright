"""MCP-tool surfaces over the registered capabilities (MCP-PROTO).

A capability that is an in-process `subgraph` (LG-3) can ALSO be exposed as an ARD `mcp_tool`: a thin FastMCP
server binds the store + models server-side and exposes the subgraph's `run_*` entrypoint as one tool
(question -> typed contract). An external agent then discovers it via ARD search and calls it as a single tool
-- saving context tokens and inter-agent coordination vs. embedding the subgraph. Same capability code, two
surfaces (subgraph for compilation, mcp_tool for cross-agent calls).

FastMCP is the server framework (grounded via the cloned-repo AST graph `graphify-out/fastmcp/graph.json` +
the installed 3.4.6 signatures, per the library-grounding rule). `compliance_server` is the reference wrapper.
"""
