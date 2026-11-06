from dify_plugin import Tool
from antom_client import execute, parse_arguments


class QueryAntom(Tool):
    def _invoke(self, tool_parameters: dict):
        arguments = parse_arguments(tool_parameters.get("arguments", "{}"))
        name = tool_parameters.get("tool_name", "")
        yield self.create_json_message(execute(self.runtime.credentials.get("access_token", ""), name, arguments))
