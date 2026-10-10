"""Paste in Langflow's Custom Component editor after the basic flow works.
This is a deterministic intent classifier for teaching; NOT a trained classifier.
"""
from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message


class LeadIntentComponent(Component):
    display_name = "SmartBiz Lead Intent"
    description = "Classify a customer question as business consultation or other."
    name = "SmartBizLeadIntent"

    inputs = [MessageTextInput(name="question", display_name="Question", value="报价多少？", tool_mode=True)]
    outputs = [Output(name="classification", display_name="Classification", method="classify")]

    def classify(self) -> Message:
        text = (self.question or "").lower()
        interested = any(keyword in text for keyword in ("价格", "报价", "购买", "试用", "合作", "订购", "费用"))
        label = "LEAD_INTENT" if interested else "GENERAL_QUESTION"
        return Message(text=label)
