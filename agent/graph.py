from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, ToolException
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import ValidationError

from .prompts import SYSTEM_PROMPT


class InvalidModelResponse(Exception):
    """The provider returned an unusable response."""


def report_tool_error(error: ToolException | ValidationError) -> str:
    if isinstance(error, ToolException):
        return str(error)
    return "Invalid monitoring tool arguments. Use a lowercase service ID and a limit from 1 to 100."


def build_graph(model: BaseChatModel, tools: list[BaseTool]) -> CompiledStateGraph:
    model_with_tools = model.bind_tools(tools)
    available = {tool.name for tool in tools}

    async def call_model(
        state: MessagesState, config: RunnableConfig
    ) -> dict[str, list[AIMessage]]:
        response = await model_with_tools.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        )
        if not isinstance(response, AIMessage) or response.invalid_tool_calls:
            raise InvalidModelResponse
        if any(call["name"] not in available for call in response.tool_calls):
            raise InvalidModelResponse("Unsupported tool requested.")
        selected = config.get("configurable", {}).get("service_id")
        if selected and any(
            call["args"].get("service") != selected for call in response.tool_calls
        ):
            raise InvalidModelResponse("Tool call outside selected service.")
        calls = sum(
            len(m.tool_calls) for m in state["messages"] if isinstance(m, AIMessage)
        )
        if calls + len(response.tool_calls) > 9:
            raise InvalidModelResponse("Investigation tool budget exceeded.")
        if not response.tool_calls:
            evidence = [
                message
                for message in state["messages"]
                if isinstance(message, ToolMessage)
            ]
            if selected and not evidence:
                raise InvalidModelResponse(
                    "Selected service investigation requires monitoring evidence."
                )
            if evidence and all(message.status == "error" for message in evidence):
                errors = dict.fromkeys(message.text for message in evidence)
                response = AIMessage(content=" ".join(errors))
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=report_tool_error))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile()
