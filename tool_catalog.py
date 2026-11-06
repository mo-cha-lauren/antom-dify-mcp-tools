"""Present the merchant's live MCP catalog and resolve workflow tool names."""

# Earlier workflows may use a documented alias instead of the server's name.
LEGACY_ALIASES = {
    "antom_inquire_merchant_profile": "get_antom_merchant_profile",
    "antom_query_transaction_detail": "get_antom_transaction_details",
    "antom_diagnose_error": "antom_error_diagnose",
    "antom_inquire_diagnosis_result": "get_antom_diagnosis_result",
    "antom_inquire_integration_logs": "get_antom_integration_logs",
    "antom_fetch_bill_download_url": "get_antom_report_downloadUrl",
}


def resolve_tool(server_tools, requested: str):
    advertised = {tool.name: tool for tool in server_tools}
    if requested in advertised:
        return advertised[requested]
    return advertised.get(LEGACY_ALIASES.get(requested))


def discovery_result(server_tools: list) -> dict:
    tools = []
    for tool in server_tools:
        entry = {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        if tool.annotations is not None:
            entry["annotations"] = tool.annotations.model_dump(mode="json", by_alias=True, exclude_unset=True)
        tools.append(entry)
    count = len(tools)
    summary = (f"Connected to Antom. The server advertised {count} tools for this authorization."
               if count else "Connected to Antom. The server returned no tools for this authorization.")
    return {
        "tools": tools, "status": "ready" if count else "no_server_tools", "summary": summary,
        "server_tool_count": count, "supported_tool_count": count, "excluded_tools": [],
    }
