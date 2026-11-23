"""LangGraph ReAct agent with human span names."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from aussiecare.config import (
    BEDROCK_MODEL_ID,
    BEDROCK_REGION,
    MODEL_NAME,
    PROVIDER_BEDROCK,
    default_provider,
    openai_http_client,
)
from aussiecare.prompts import system_prompt_for
from aussiecare.tools import tools_for_scenario
from aussiecare.tracing import GRAPH_AGENT_NAME, LLM_SPAN_NAME


def build_agent(scenario_id: str, provider: str | None = None, customer: dict | None = None):
    provider = provider or default_provider()
    if provider == PROVIDER_BEDROCK:
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(
            model=BEDROCK_MODEL_ID,
            region_name=BEDROCK_REGION,
            name=LLM_SPAN_NAME,
        )
    else:
        model = ChatOpenAI(
            model=MODEL_NAME,
            temperature=0,
            name=LLM_SPAN_NAME,
            http_client=openai_http_client(),
        )
    tools = tools_for_scenario(scenario_id)
    prompt = system_prompt_for(scenario_id, customer)
    try:
        return create_react_agent(
            model,
            tools,
            prompt=prompt,
            name=GRAPH_AGENT_NAME,
        )
    except TypeError:
        return create_react_agent(model, tools, prompt=prompt)


def last_ai_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            if isinstance(message.content, str):
                return message.content
            return str(message.content)
    return ""


def run_turn(
    scenario_id: str,
    user_text: str,
    provider: str | None = None,
    callbacks=None,
    mode: str = "normal",
    customer: dict | None = None,
) -> str:
    agent = build_agent(scenario_id, provider, customer)
    config: dict = {
        "metadata": {"scenario": scenario_id, "mode": mode},
        "tags": [f"scenario:{scenario_id}", f"mode:{mode}"],
    }
    if callbacks:
        config["callbacks"] = list(callbacks)
    result = agent.invoke({"messages": [HumanMessage(content=user_text)]}, config=config)
    return last_ai_text(result["messages"])
