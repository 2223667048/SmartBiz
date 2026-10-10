"""Copy the WHOLE file into Langflow's New Custom Component code editor.

Runs inside the Langflow container. Docker DNS resolves `smartbiz` on the
Compose network. Only queries a public, read-only demo catalog endpoint.
The model may supply a query, but NOT a URL, credential, session ID, or SQL.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lfx.custom import Component
from lfx.io import MessageTextInput, Output
from lfx.schema import Data


class SmartBizCatalogTool(Component):
    display_name = "SmartBiz 服务目录查询"
    description = "只读查询 SmartBiz 演示服务目录；用于确认提供哪些服务。没有真实报价，不查询客户或工单。"
    icon = "search"
    name = "SmartBizCatalogTool"

    inputs = [
        MessageTextInput(
            name="query",
            display_name="查询关键词",
            info="输入知识库、客服、工单等关键词；仅查询公开演示数据。",
            value="",
            tool_mode=True,
        ),
    ]
    outputs = [
        Output(name="result", display_name="服务目录结果", method="search_services"),
    ]

    def search_services(self) -> Data:
        query = str(self.query or "").strip()
        if len(query) > 80:
            return Data(data={"error": "查询不得超过80字符"})

        # Only this fixed, internal service endpoint can be reached by the tool.
        endpoint = "http://smartbiz:8000/api/catalog/search?" + urlencode({"q": query})
        req = Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(req, timeout=5) as res:
                payload = json.loads(res.read(32_768).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            self.status = "服务目录访问失败"
            return Data(data={"error": "服务目录暂不可用，请稍后重试"})

        self.status = "查询完成（演示数据）"
        return Data(data=payload)
