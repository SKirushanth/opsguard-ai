import json
import logging
import re
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, ToolException
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import ValidationError

from investigation import CLARIFICATION, Evidence, Investigation, Selection

from .prompts import SYSTEM_PROMPT

logger = logging.getLogger("uvicorn.error").getChild("opsguard.agent")


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
        messages = state["messages"]
        options = config.get("configurable", {})
        selected = options.get("service_id")
        demo = bool(selected and selected.startswith("demo-ai-"))
        prior_calls = {
            call["id"]: call for msg in messages if isinstance(msg, AIMessage) for call in msg.tool_calls
        }
        results = [msg for msg in messages if isinstance(msg, ToolMessage)]
        if demo and not prior_calls:
            # The visitor's target is already known. Collect its frozen evidence
            # before asking the model to select references.
            return {"messages": [AIMessage(content="", tool_calls=[
                {"name": "get_service_" + resource, "id": uuid4().hex, "args": {"service": selected}}
                for resource in ("health", "logs", "metrics")
            ])]}
        observed, resources, errors = {}, [], []
        for msg in results:
            call = prior_calls[msg.tool_call_id]
            selected = selected or call["args"]["service"]
            resource = call["name"].removeprefix("get_service_")
            if resource not in resources:
                resources.append(resource)
            if msg.status == "error":
                logger.info("agent_monitoring_read_failed resource=%s", resource)
                observed.pop(resource, None)
                errors.append(msg.text)
            else:
                observed[resource] = json.loads(msg.text)

        evidence = Evidence.collect(selected or "", observed, errors)

        def finish(selection=None):
            if selection is not None and not options.get("conversational"):
                selection = selection.model_copy(update={"style": None})
            cited_resources = list(observed) if demo else resources
            if demo and "health" not in observed:
                raise InvalidModelResponse("Selected demo health evidence is unavailable.")
            investigation = Investigation(service_id=selected, resources=cited_resources, selection=selection)
            text = evidence.render(selection, draft=options.get("draft", False))
            if not observed and errors:
                text = " ".join(dict.fromkeys(errors))
            answer = AIMessage(
                content=text,
                additional_kwargs={"investigation": investigation.model_dump()},
            )
            return {"messages": [answer]}

        # If a diagnosis started with a health lookup, read the missing diagnostic
        # resources before deciding that there is no evidence. Only user input
        # (never a tool message) determines whether to broaden the read.
        if resources == ["health"] and ("health" in observed or options.get("conversational")):
            question = messages[0].text.removeprefix(f"Selected monitoring service ID: {selected}\n")
            diagnosis = re.search(
                r"\b(why|investigate|investigation|cause|diagnose|diagnosis|evidence|logs?|metrics?|troubleshoot|recommend)\b",
                question, re.I,
            )
            if options.get("draft") or diagnosis:
                calls = [
                    {"name": "get_service_" + name, "id": uuid4().hex, "args": {"service": selected}}
                    for name in ("logs", "metrics")
                ]
                return {"messages": [AIMessage(content="", tool_calls=calls)]}

        if results and not evidence.diagnostic:
            logger.info("agent_selection_fallback reason=no_diagnostic_evidence")
            return finish()

        prompt = SYSTEM_PROMPT
        if options.get("service_id"):
            prompt += "\nServer-selected monitoring service ID: " + options["service_id"]
        if results:
            # Only application-generated IDs and allowed categories enter the system message.
            catalog = {
                "observations": list(evidence.facts),
                "supported_hypotheses": {ref: sorted(causes) for ref, causes in evidence.supports.items()},
            }
            prompt += "\nAvailable evidence references: " + json.dumps(catalog)
            if options.get("conversational"):
                prompt += (
                    '\nAlso include "style": "simple", "summary", "technical", or "troubleshooting" '
                    "to match the latest question. Use earlier questions only to interpret follow-ups. "
                    "Select only the evidence and supported causes relevant to this reply."
                )
        response = await model_with_tools.ainvoke([SystemMessage(content=prompt), *messages])
        if not isinstance(response, AIMessage) or response.invalid_tool_calls:
            if results:
                logger.info("agent_selection_fallback reason=invalid_model_reply")
                return finish()
            raise InvalidModelResponse

        if response.tool_calls:
            if any(call["name"] not in available for call in response.tool_calls):
                raise InvalidModelResponse("Unsupported tool requested.")
            services = [call["args"].get("service") for call in response.tool_calls]
            if not all(isinstance(service, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service) for service in services):
                raise InvalidModelResponse("Invalid monitoring service ID.")
            targets = set(services)
            if len(targets) != 1 or (selected and targets != {selected}):
                raise InvalidModelResponse("Tool call outside selected service.")
            if len(prior_calls) + len(response.tool_calls) > 9:
                raise InvalidModelResponse("Investigation tool budget exceeded.")
            if any(call["name"].removeprefix("get_service_") in resources for call in response.tool_calls):
                raise InvalidModelResponse("Repeated monitoring read.")
            ids = [call["id"] for call in response.tool_calls]
            if not all(ids) or len(ids) != len(set(ids)) or prior_calls.keys() & set(ids):
                raise InvalidModelResponse("Ambiguous tool response reference.")
            return {"messages": [response]}

        if not results:
            if selected:
                raise InvalidModelResponse("Selected service investigation requires monitoring evidence.")
            return {"messages": [AIMessage(content=CLARIFICATION)]}
        try:
            selection = Selection.model_validate_json(response.text)
        except ValidationError:
            logger.info("agent_selection_fallback reason=invalid_selection_json")
            return finish()
        if not evidence.accepts(selection):
            logger.info("agent_selection_fallback reason=unsupported_references")
            return finish()
        if options.get("conversational") and selection.style is None:
            logger.info("agent_selection_fallback reason=missing_answer_style")
        return finish(selection)

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=report_tool_error))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile()
