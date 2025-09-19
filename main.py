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
from blog_agent.custom_runner import FallbackAgentRunner
from agents.run import set_default_agent_runner

# Set up the custom runner as the default
custom_runner = FallbackAgentRunner()
set_default_agent_runner(custom_runner)

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
    try:
        # If it's a string, return it directly
        if isinstance(result, str):
            return {"final_output": result}
        
        # If it has a dict method, use that
        if hasattr(result, "dict") and callable(getattr(result, "dict")):
            return result.dict()
        
        # If it has __dict__, try to serialize its attributes
        if hasattr(result, "__dict__"):
            result_dict = {}
            for key, value in vars(result).items():
                try:
                    # Try to serialize the value
                    if isinstance(value, (str, int, float, bool, type(None))):
                        result_dict[key] = value
                    elif isinstance(value, (list, dict)):
                        # For lists and dicts, try to serialize them
                        result_dict[key] = value
                    else:
                        # For complex objects, convert to string
                        result_dict[key] = str(value)
                except:
                    # If we can't serialize, convert to string
                    result_dict[key] = f"<Non-serializable: {type(value).__name__}>"
            return result_dict
        
        # Handle basic types
        elif isinstance(result, (int, float, bool, type(None))):
            return {"final_output": result}
        elif isinstance(result, list):
            # Try to serialize list items
            serialized_list = []
            for item in result:
                try:
                    if isinstance(item, (str, int, float, bool, type(None))):
                        serialized_list.append(item)
                    else:
                        serialized_list.append(str(item))
                except:
                    serialized_list.append(f"<Non-serializable: {type(item).__name__}>")
            return {"final_output": serialized_list}
        elif isinstance(result, dict):
            # Try to serialize dict values
            serialized_dict = {}
            for key, value in result.items():
                try:
                    if isinstance(value, (str, int, float, bool, type(None))):
                        serialized_dict[key] = value
                    elif isinstance(value, (list, dict)):
                        serialized_dict[key] = value
                    else:
                        serialized_dict[key] = str(value)
                except:
                    serialized_dict[key] = f"<Non-serializable: {type(value).__name__}>"
            return {"final_output": serialized_dict}
        
        # Fallback: convert to string
        else:
            return {"final_output": str(result)}
            
    except Exception as e:
        # If any error occurs during serialization, return a safe representation
        return {"final_output": f"Result could not be serialized: {str(e)}", "error": str(e)}

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
        # Use the global custom runner instance
        result = await combined_research_workflow(
            LLM_MODELS=custom_runner.LLM_MODELS, 
            is_model_available=custom_runner.is_model_available, 
            get_model_by_name=custom_runner.get_model_by_name, 
            increment_usage=custom_runner.increment_usage, 
            MAX_TURNS=15
        )
        
        # Extract a simple success message instead of trying to serialize the full result
        success_message = "Research completed successfully and data added to research_data worksheet."
        if isinstance(result, dict) and "error" in result:
            success_message = f"Research completed with issues: {result['error']}"
        elif hasattr(result, 'final_output'):
            output = str(result.final_output)
            if output.strip():  # If there's actual content
                success_message = output
            else:
                success_message = "Research completed, but no new data was added. This may be because there are no available keywords in the ContentSpark_Keywords worksheet."
        elif isinstance(result, str):
            if result.strip():  # If there's actual content
                success_message = result
            else:
                success_message = "Research completed, but no new data was added. This may be because there are no available keywords in the ContentSpark_Keywords worksheet."
        else:
            # For other types of results, try to extract meaningful information
            result_str = str(result)
            if "error" in result_str.lower():
                success_message = f"Research completed with issues: {result_str}"
            elif result_str.strip():
                success_message = result_str
            else:
                success_message = "Research completed, but no new data was added. This may be because there are no available keywords in the ContentSpark_Keywords worksheet."
            
        return {"status": "success", "message": success_message}
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
        # Use the global custom runner instance
        result = await custom_runner.run_with_fallback(
            brief_agent,
            agent_input,
            max_turns=MAX_TURNS
        )
        
        # Extract a simple success message
        success_message = "Content brief generation completed successfully."
        if hasattr(result, 'final_output'):
            output = str(result.final_output)
            # Check if there's an error message in the output
            if "error" in output.lower() or "no ungenerated rows found" in output.lower():
                success_message = f"Content brief generation completed with issues: {output}"
            elif output.strip():  # If there's actual content
                success_message = output
            else:
                success_message = "Content brief generation completed, but no new briefs were created. This may be because there are no approved research findings in the research_data worksheet that haven't been generated yet."
        elif isinstance(result, str):
            if "error" in result.lower() or "no ungenerated rows found" in result.lower():
                success_message = f"Content brief generation completed with issues: {result}"
            elif result.strip():  # If there's actual content
                success_message = result
            else:
                success_message = "Content brief generation completed, but no new briefs were created. This may be because there are no approved research findings in the research_data worksheet that haven't been generated yet."
        else:
            # For other types of results, try to extract meaningful information
            result_str = str(result)
            if "error" in result_str.lower():
                success_message = f"Content brief generation completed with issues: {result_str}"
            elif result_str.strip():
                success_message = result_str
            else:
                success_message = "Content brief generation completed, but no new briefs were created. This may be because there are no approved research findings in the research_data worksheet that haven't been generated yet."
            
        return {"status": "success", "message": success_message}
    except Exception as e:
        print(f"Error in /generate_brief: {e}")
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
        # Use the global custom runner instance
        result = await custom_runner.run_with_fallback(
            content_generator_agent,
            agent_input, # Use the processed input
            max_turns=MAX_TURNS
        )
        
        # Extract a simple success message
        success_message = "Content generation completed successfully."
        if hasattr(result, 'final_output'):
            output = str(result.final_output)
            # Check if there's an error message in the output
            if "error" in output.lower() or "no ungenerated briefs found" in output.lower():
                success_message = f"Content generation completed with issues: {output}"
            elif output.strip():  # If there's actual content
                success_message = output
            else:
                success_message = "Content generation completed, but no new content was created. This may be because there are no approved briefs in the content_briefs worksheet that haven't been generated yet."
        elif isinstance(result, str):
            if "error" in result.lower() or "no ungenerated briefs found" in result.lower():
                success_message = f"Content generation completed with issues: {result}"
            elif result.strip():  # If there's actual content
                success_message = result
            else:
                success_message = "Content generation completed, but no new content was created. This may be because there are no approved briefs in the content_briefs worksheet that haven't been generated yet."
        else:
            # For other types of results, try to extract meaningful information
            result_str = str(result)
            if "error" in result_str.lower():
                success_message = f"Content generation completed with issues: {result_str}"
            elif result_str.strip():
                success_message = result_str
            else:
                success_message = "Content generation completed, but no new content was created. This may be because there are no approved briefs in the content_briefs worksheet that haven't been generated yet."
            
        return {"status": "success", "message": success_message}
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
        
        # Extract a simple success message
        if isinstance(result, dict):
            if result.get("status") == "no_posts_found":
                success_message = "No approved posts found ready for publishing. Please approve a post in the generated_posts worksheet and set Published to 'No'."
            elif result.get("status") == "error":
                success_message = f"Error during posting: {result.get('error', 'Unknown error')}"
            else:
                success_message = str(result.get("data", "Content posting process completed."))
        elif hasattr(result, 'final_output'):
            success_message = str(result.final_output)
        elif isinstance(result, str):
            success_message = result
        else:
            success_message = "Content posting process completed."
            
        return {"status": "success", "message": success_message}
    except Exception as e:
        print(f"Error in /post_content: {e}")
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")