from dify_plugin import Tool
from antom_client import execute


class DiscoverAntomTools(Tool):
    def _invoke(self, tool_parameters: dict):
        result = execute(self.runtime.credentials.get("access_token", ""))
        yield self.create_json_message(result)
        yield self.create_text_message(result["summary"])
