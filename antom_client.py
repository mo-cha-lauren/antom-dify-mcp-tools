"""Fixed-origin MCP client; schemas are discovered, never copied into the package."""
import asyncio
import json
from urllib.parse import urlsplit

import httpx2
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp_types import Implementation, PaginatedRequestParams
from tool_catalog import ALLOWED_TOOLS, discovery_result, resolve_tool

SERVER_URL = "https://mcp.antom.com"
ISSUER = "https://dashboard-apiv2.antom.com"
MAX_ARGUMENT_BYTES = 32768


class AntomError(Exception):
    """Messages are safe to display; never attach upstream response bodies."""


def require_origin(url: str, origin: str) -> str:
    try:
        if not isinstance(url, str):
            raise ValueError()
        parsed = urlsplit(url)
        expected = urlsplit(origin)
        valid = (parsed.scheme == "https" and parsed.hostname == expected.hostname
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password
                 and not parsed.fragment and not any(c.isspace() for c in url))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise AntomError("Antom returned an unsupported network destination.")
    return url


def parse_arguments(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode()) > MAX_ARGUMENT_BYTES:
        raise AntomError("Arguments must be a JSON object of at most 32 KiB.")
    try:
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise AntomError("Arguments must be valid JSON.") from None
    if not isinstance(value, dict):
        raise AntomError("Arguments must be a JSON object.")
    return value


def validate_arguments(schema: dict, arguments: dict) -> None:
    def check_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("$ref", "$dynamicRef") and (not isinstance(item, str) or not item.startswith("#")):
                    raise AntomError("External JSON Schema references are not supported.")
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    try:
        check_refs(schema)
        Draft202012Validator.check_schema(schema)
        if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
            raise AntomError("Arguments do not match the live tool schema. Discover tools again.")
    except AntomError:
        raise
    except Exception:
        raise AntomError("The server returned an unsupported tool schema.") from None


async def _guard_mcp_request(request):
    require_origin(str(request.url), SERVER_URL)


async def _execute(token: str, tool_name: str | None, arguments: dict | None):
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise AntomError("Authorize your Antom account in the plugin settings first.")
    if tool_name is not None and tool_name not in ALLOWED_TOOLS:
        raise AntomError("This tool is outside the plugin's query and diagnosis allowlist.")
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token}, timeout=30,
        follow_redirects=False, event_hooks={"request": [_guard_mcp_request]},
    ) as http:
        async with streamable_http_client(SERVER_URL, http_client=http) as streams:
            async with ClientSession(
                *streams, read_timeout_seconds=30,
                client_info=Implementation(name="antom-dify-mcp-tools", version="0.0.3"),
            ) as session:
                await session.initialize()
                server_tools = []
                cursor = None
                for _ in range(20):
                    page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
                    server_tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor:
                        break
                else:
                    raise AntomError("Tool discovery exceeded the page limit.")
                if tool_name is None:
                    return discovery_result(server_tools)
                target = resolve_tool(server_tools, tool_name)
                if target is None:
                    raise AntomError("The selected tool is unavailable to this plugin. Discover tools to check exclusions.")
                validate_arguments(target.input_schema, arguments or {})
                result = await session.call_tool(target.name, arguments or {})
                if result.is_error:
                    raise AntomError("Antom could not complete the query. Check account permissions and inputs.")
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def execute(token: str, tool_name: str | None = None, arguments: dict | None = None):
    async def bounded():
        async with asyncio.timeout(100):
            return await _execute(token, tool_name, arguments)
    try:
        result = asyncio.run(bounded())
        if len(json.dumps(result).encode()) > 524288:
            raise AntomError("The response exceeds 512 KiB. Narrow your query.")
        return result
    except AntomError:
        raise
    except Exception as error:
        # Async transport task groups wrap application errors. Recover only our
        # own safe messages; never display a third-party exception or response.
        def safe_message(exc):
            if isinstance(exc, AntomError):
                return str(exc)
            if isinstance(exc, BaseExceptionGroup):
                for nested in exc.exceptions:
                    message = safe_message(nested)
                    if message:
                        return message
            return None
        message = safe_message(error)
        if message:
            raise AntomError(message) from None
        raise AntomError("Antom MCP is unavailable or authorization has expired. Reauthorize and retry.") from None
