import os
from tavily import TavilyClient
from agents import function_tool
from typing import List, Optional, Dict, Any, Union
import requests
from bs4 import BeautifulSoup

# Initialize Tavily Client
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
if not TAVILY_API_KEY:
    raise ValueError("TAVILY_API_KEY environment variable not set.")
tavily_client = TavilyClient(TAVILY_API_KEY)

# --- Tool Functions ---
@function_tool
def tavily_search_tool(query: str, max_results: int = 5, topic: str = "general", search_depth: str = "basic") -> Dict[str, Any]:
    """
    Execute a Tavily search query and return structured results.

    Args:
        query (str): The search query.
        max_results (int, optional): Maximum number of results to return. Defaults to 5.
        topic (str, optional): The search topic ('general', 'news'). Defaults to "general".
        search_depth (str, optional): Depth of the search ('basic', 'advanced'). Defaults to "basic".

    Returns:
        Dict[str, Any]: A dictionary containing the query, results (list of dicts with url, title, content, score), and response_time.
                        Returns a dict with an 'error' key if an exception occurs.
    """
    try:
        response = tavily_client.search(
            query,
            max_results=max_results,
            topic=topic,
            search_depth=search_depth
        )
        return {
            "query": response["query"],
            "results": [
                {
                    "url": result["url"],
                    "title": result["title"],
                    "content": result["content"],
                    "score": result["score"]
                }
                for result in response["results"]
            ],
            "response_time": response["response_time"]
        }
    except Exception as e:
        return {"error": str(e), "results": [], "response_time": None}

@function_tool
def tavily_extract_tool(urls: List[str], include_images: bool = False) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Extract content from a list of URLs using Tavily Extract API.

    Args:
        urls (List[str]): List of URLs to extract content from.
        include_images (bool, optional): Whether to include images in the extracted content. Default is False.

    Returns:
        Union[List[Dict[str, Any]], Dict[str, Any]]: A list of dictionaries containing url and content for each URL,
                                                     or a dict with an 'error' key if an exception occurs.
    """
    try:
        response = tavily_client.extract(urls=urls, include_images=include_images)
        
        # Check if response is a list (expected case)
        if isinstance(response, list):
            return [
                {
                    "url": result["url"],
                    "title": result.get("title", ""),
                    "content": result["content"],
                    "images": result.get("images", []) if include_images else []
                }
                for result in response
            ]
        else:
            # Handle case where response might be an error dictionary
            return {"error": f"Unexpected response format: {response}", "results": []}
    except Exception as e:
        return {"error": str(e), "results": []}

@function_tool
def tavily_crawl_tool(start_url: str, max_depth: int = 2, limit: int = 10, instructions: Optional[str] = None) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Crawl a website starting from a given URL using Tavily Crawl API.

    Args:
        start_url (str): The starting URL for the crawl.
        max_depth (int, optional): The maximum depth to crawl. Defaults to 2.
        limit (int, optional): The maximum number of pages to crawl. Defaults to 10.
        instructions (str, optional): Specific instructions for the crawler. Defaults to None.

    Returns:
        Union[List[Dict[str, Any]], Dict[str, Any]]: A list of dictionaries containing url and raw_content for each crawled page,
                                                     or a dict with an 'error' key if an exception occurs.
    """
    try:
        response = tavily_client.crawl(
            url=start_url,
            max_depth=max_depth,
            limit=limit,
            instructions=instructions
        )
        return [
            {
                "url": result["url"],
                "raw_content": result["raw_content"]
            }
            for result in response["results"]
        ]
    except Exception as e:
        return {"error": str(e), "results": []}

@function_tool
def fetch_url_title(url: str) -> str:
    """
    Fetch the title of a webpage for fact-checking or display.

    Args:
        url (str): The URL of the webpage.

    Returns:
        str: The title of the webpage, or the URL itself if the title cannot be fetched.
    """
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status() # Raise an exception for bad status codes
        soup = BeautifulSoup(response.text, "html.parser")
        return soup.title.string.strip() if soup.title and soup.title.string else url
    except Exception:
        return url # Return URL if title fetching fails


@function_tool
def x_search_tool(keyword: str):
    """Searches recent posts (past 7 days) on X for trending topics.
    
    Args:
        keyword (str): The keyword to search for.
    """
    try:
        bearer_token = os.environ.get("X_API_BEARER_TOKEN")
        if not bearer_token:
            logging.error("X_API_BEARER_TOKEN is not set")
            return {"error": "X_API_BEARER_TOKEN is not set"}

        headers = {
            "Authorization": f"Bearer {bearer_token}"
        }

        # Calculate 'since' date (7 days ago)
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        params = {
            "query": f"{keyword} lang:en since:{seven_days_ago}",
            "max_results": 10
        }

        url = "https://api.twitter.com/2/tweets/search/recent"
        logging.info(f"Querying X with: {params}")
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        data = response.json()
        if "errors" in data:
            logging.error(f"API error: {data['errors'][0]['message']}")
            return {"error": data["errors"][0]["message"]}
        posts = data.get("data", [])
        logging.info(f"Found {len(posts)} posts")
        return [post.get("text", "") for post in posts]
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {str(e)}")
        return {"error": f"Request failed: {str(e)}"}

@function_tool
def web_search_tool(keyword: str):
    """Searches web content (past 30 days) for trending topics.

        Args:
        keyword (str): The keyword to search for.
    """
    try:
        params = {"q": f"trending topics {keyword}", "api_key": os.environ["SERPAPI_KEY"], "num": 5, "tbs": "qdr:m"}
        response = requests.get("https://serpapi.com/search", params=params)
        response.raise_for_status()
        return [result['title'] for result in response.json().get('organic_results', [])]
    except Exception as e:
        return {"error": f"Web search failed: {str(e)}"}