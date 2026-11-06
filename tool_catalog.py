"""The six supported capabilities, using exact case-sensitive MCP identifiers."""

DOCUMENTED_TO_WIRE = {
    "antom_inquire_merchant_profile": "get_antom_merchant_profile",
    "antom_query_transaction_detail": "get_antom_transaction_details",
    "antom_diagnose_error": "antom_error_diagnose",
    "antom_inquire_diagnosis_result": "get_antom_diagnosis_result",
    "antom_inquire_integration_logs": "get_antom_integration_logs",
    "antom_fetch_bill_download_url": "get_antom_report_downloadUrl",
}
ALLOWED_TOOLS = frozenset(DOCUMENTED_TO_WIRE) | frozenset(DOCUMENTED_TO_WIRE.values())


def exclusion_reason(tool) -> str | None:
    if tool.name not in ALLOWED_TOOLS:
        return "unsupported_tool_name"
    if tool.annotations is not None and tool.annotations.read_only_hint is False:
        return "server_marks_non_read_only"
    return None


def resolve_tool(server_tools, requested: str):
    advertised = {tool.name: tool for tool in server_tools}
    target = advertised.get(requested)
    if target is None and requested in DOCUMENTED_TO_WIRE:
        target = advertised.get(DOCUMENTED_TO_WIRE[requested])
    if target is None or exclusion_reason(target):
        return None
    return target


def discovery_result(server_tools: list) -> dict:
    tools, excluded = [], []
    for tool in server_tools:
        reason = exclusion_reason(tool)
        if reason:
            excluded.append({"name": tool.name, "reason": reason})
        else:
            tools.append({"name": tool.name, "description": tool.description, "input_schema": tool.input_schema})
    if not server_tools:
        status = "no_server_tools"
        summary = "Connected to Antom, but the server returned no tools for this authorization."
    elif not tools:
        status = "no_supported_tools"
        summary = (f"Connected to Antom. The server returned {len(server_tools)} tools, "
                   "but none meet this plugin's query allowlist. See excluded_tools for names and reasons.")
    else:
        status = "ready"
        summary = f"Connected to Antom. {len(tools)} of {len(server_tools)} server tools are available as queries."
    return {
        "tools": tools, "status": status, "summary": summary,
        "server_tool_count": len(server_tools), "supported_tool_count": len(tools),
        "excluded_tools": excluded,
    }
