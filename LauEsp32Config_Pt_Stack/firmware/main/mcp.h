// MCP over Streamable HTTP, stateless, plain JSON responses (SD_BITSTREAM_LIBRARY.md §6.1):
// initialize, notifications/*, ping, tools/list, tools/call.
#pragma once

// Handle one JSON-RPC message (the POST body). Returns a malloc'd JSON response for the HTTP
// body, or NULL when the message was a notification (reply 202 Accepted with no body).
char *mcp_handle(const char *body);
