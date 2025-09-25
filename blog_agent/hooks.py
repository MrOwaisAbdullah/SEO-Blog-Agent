from agents import Agent, AgentHooks, RunContextWrapper, Tool


class MyAgentHooks(AgentHooks):
    async def on_handoff(self, context: RunContextWrapper, agent: Agent, source: Agent):
        agent_name = agent.name
        print("--------------------------------")
        print(f"Handing off to {agent_name}...")
        print("--------------------------------")

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent):
        print("--------------------------------")
        print(f"[Hook] Agent start: {agent.name}")
        # Print input length if possible
        if hasattr(context, 'input') and isinstance(context.input, list):
            print(f"[Hook] Input length for agent '{agent.name}': {len(context.input)}")
        elif hasattr(context, 'input'):
            print(f"[Hook] Input type for agent '{agent.name}': {type(context.input)}")
        print("--------------------------------")

    async def on_agent_end(self, context, agent, result):
        print("--------------------------------")
        print(f"[Hook] Agent end: {agent.name}")
        print("--------------------------------")

    async def on_tool_start(self, context: RunContextWrapper, agent: Agent, tool: Tool):
        print("--------------------------------")
        print(f"[Hook] Tool start: {tool.name} in agent '{agent.name}'")
        print("--------------------------------")

    async def on_tool_end(self, context: RunContextWrapper, agent: Agent, tool: Tool, result):
        print("--------------------------------")
        print(f"[Hook] Tool end: {tool.name} in agent '{agent.name}'")
        print(f"[Hook] Tool result: {result}")
        print("--------------------------------")