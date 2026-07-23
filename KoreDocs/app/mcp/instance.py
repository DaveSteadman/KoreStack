# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Singleton FastMCP instance shared across all KoreDocs MCP modules.
#
# Imported by every MCP sub-module so that all tool registrations target the same
# FastMCP instance, which is then mounted into the FastAPI app by server.py.
#
# Related modules:
#   - app/_mcp_shared.py    -- re-exports mcp; imports this
#   - app/koredocs_mcp.py   -- imports mcp from this module
#   - app/server.py         -- mounts this mcp instance
# ====================================================================================================

from fastmcp import FastMCP

mcp = FastMCP('KoreDocs')
