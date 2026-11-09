from dify_plugin import Tool
from antom_client import AntomError, execute, parse_arguments


class QueryAntom(Tool):
    def _invoke(self, tool_parameters: dict):
        name = tool_parameters.get("tool_name")
        if not isinstance(name, str) or not name.strip():
            raise AntomError("Enter a tool name returned by discovery.")
        arguments = parse_arguments(tool_parameters.get("arguments", "{}"))
        yield self.create_json_message(execute(self.runtime.credentials.get("access_token", ""), name, arguments))
