# main.py
from fastapi import FastAPI, HTTPException, Security, Depends, Body
from fastapi.security import APIKeyHeader
import os
from blog_agent.blog_agents import content_generator_agent, brief_agent 
from blog_agent.llm_clients import run_flow_with_agent_fallback, LLM_MODELS, is_model_available, get_model_by_name, increment_usage
from dotenv import load_dotenv
from typing import Any, Dict, Optional, Union
import asyncio
from blog_agent.research_agent import combined_research_workflow
from blog_agent.posting_agent import run_posting_workflow

load_dotenv()

app = FastAPI()

# Define API key header
API_KEY_NAME = "Authorization"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# --- Dependency for API Key Verification ---
EXPECTED_API_KEY = os.environ.get("API_KEY") # Get the key from environment variables

async def verify_api_key(api_key: str = Depends(api_key_header)):
    """
    Verifies the API key provided in the Authorization header.
    Expected format: "Bearer <your_api_key>"
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="Authorization header is missing")
    if not api_key.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must start with 'Bearer '")
    token = api_key[len("Bearer "):] # Extract the token part
    if token != EXPECTED_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key # Return the key or just indicate success


# --- Helper Function ---
def serialize_result(result: Any) -> Dict[str, Any]:
    """Attempts to serialize the agent run result into a dictionary."""
    if hasattr(result, "dict"):
        return result.dict()
    elif hasattr(result, "__dict__"):
        return vars(result)
    elif isinstance(result, (str, int, float, bool, type(None))):
        # Handle basic types directly
        return {"final_output": result}
    elif isinstance(result, (list, dict)):
         # Assume lists/dicts are serializable, or contain serializable items
         return {"final_output": result}
    else:
        # If it's a complex type or already serializable, return as is
        # Or convert to string representation if necessary
        try:
            # FastAPI/Starlette can usually handle basic types (str, int, float, bool, list, dict)
            # This is a fallback for unknown types.
            return {"final_output": str(result)}
        except:
            return {"final_output": "Result could not be serialized"}

# --- Endpoints ---

# Root endpoint
@app.get("/")
async def root():
    return {"message": "Welcome to the SEO Blog Agent API"}

@app.get("/health")
async def health():
    return {"status": "OK", "message": "The server is healthy"}

# --- Constants for fallback runner ---
MAX_RETRIES = 10 # Adjust as needed based on agent complexity
MAX_TURNS = 30 # Adjust as needed based on agent complexity


@app.get("/research")
async def research_topic(api_key: str = Security(verify_api_key)):
    """
    Executes the Researcher Agent with a given query using fallbacks.
    Expects a JSON body like: {"input_query": "Latest trends in AI marketing"}
    """
    try:
        result = await combined_research_workflow(LLM_MODELS=LLM_MODELS, is_model_available=is_model_available, get_model_by_name=get_model_by_name, increment_usage=increment_usage, MAX_TURNS=15)
        serialized_result = serialize_result(result)
        return {"status": "success", "message": "Research completed.", "result": serialized_result}
    except Exception as e:
        print(f"Error in /research: {e}")
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")


@app.get("/generate_brief")
async def generate_brief(api_key: str = Security(verify_api_key)):
    """
    Executes the Content brief Generator Agent with a given research findings using fallbacks.
    Expects a JSON body like: {"input_brief": "Write a blog post about..."}
    Or {"input_brief": { "keyword": "...", "outline": "...", ...}}
    """

    agent_input = f"Generate content brief based on the first approved research findings that is not generated yet from the research_data worksheet."

    try:
        result = await run_flow_with_agent_fallback(
            brief_agent,
            agent_input,
            LLM_MODELS,
            is_model_available,
            get_model_by_name,
            increment_usage,
            max_retries=MAX_RETRIES,
            max_turns=MAX_TURNS
        )
        serialized_result = serialize_result(result)
        return {"status": "success", "message": "Content generation completed.", "result": serialized_result}
    except Exception as e:
        print(f"Error in /generate_content: {e}")
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")


@app.get("/generate_content")
async def generate_content(api_key: str = Security(verify_api_key)):
    """
    Executes the Content Generator Agent with a given brief using fallbacks.
    Expects a JSON body like: {"input_brief": "Write a blog post about..."}
    Or {"input_brief": { "keyword": "...", "outline": "...", ...}}
    """

    # You might want a more structured prompt based on the dict keys
    agent_input = f"Generate content based on the first approved brief that is not generated yet from the content_briefs worksheet and add it to the generated_posts worksheet."

    try:
        result = await run_flow_with_agent_fallback(
            content_generator_agent,
            agent_input, # Use the processed input
            LLM_MODELS,
            is_model_available,
            get_model_by_name,
            increment_usage,
            max_retries=MAX_RETRIES,
            max_turns=MAX_TURNS
        )
        serialized_result = serialize_result(result)
        return {"status": "success", "message": "Content generation completed.", "result": serialized_result}
    except Exception as e:
        print(f"Error in /generate_content: {e}")
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")

@app.get("/post_content")
async def post_content(api_key: str = Security(verify_api_key)):
    """
    Executes the Posting Agent using fallbacks.
    Expects a JSON body containing the necessary data for posting
    (e.g., title, content, categories, image_path, etc.).
    The structure should match what the Posting Agent expects.
    Example body:
    {
      "title": "My Blog Post",
      "summary": "A summary...",
      "content": "The full markdown content...",
      "categories": ["AI", "Marketing"],
      ...
    }
    """
    try:
        prompt_for_agent = f"Post the content from the generated_posts worksheet to the blog platform."
        result = await run_posting_workflow()
        serialized_result = serialize_result(result)
        return {"status": "success", "message": "Content posting process initiated/completed.", "result": serialized_result}
    except Exception as e:
        print(f"Error in /post_content: {e}")
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")