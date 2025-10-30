# sanity_adapter.py
import os
import requests
import time
from datetime import datetime
import logging
import json
import uuid
import re
import mimetypes
import urllib.parse
from typing import Dict, Any, Optional, List, Union
from lib.markdown_parser import markdown_to_sanity_blocks
# Configure logging for this module
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SanityAdapter:
    """A reusable adapter for posting content to Sanity.io, handling schema correctly."""

    def __init__(self, project_id: str, dataset: str, token: str):
        if not all([project_id, dataset, token]):
            raise ValueError("project_id, dataset, and token must be provided.")

        self.project_id = project_id
        self.dataset = dataset
        self.token = token
        # Use the base URL for the Actions API
        self.base_url = f"https://{self.project_id}.api.sanity.io/v2021-03-25"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }


    def fetch_internal_links(self, topic: str, max_results: int = 3, exclude_slug: str = None, max_retries: int = 2) -> List[Dict[str, str]]:
        """
        Fetches related posts from Sanity CMS for a given topic, extracting keywords from sentence-based topics to match against category titles.
        Args:
            topic: Topic for the topic cluster (e.g., "What is AI agents").
            max_results: Maximum number of posts to return (default: 3).
            exclude_slug: Slug of the current post to exclude.
            max_retries: Number of retry attempts.
        Returns:
            List of dictionaries with title and slug (e.g., [{"title": "AI Tips", "slug": "/blog/ai-tips"}]).
        """
        from slugify import slugify

        # Preprocess topic to extract keywords
        stop_words = {"what", "is", "are", "the", "a", "an", "in", "to", "for", "and", "of"}
        topic_words = [word.lower() for word in topic.lower().split() if word not in stop_words]
        if not topic_words:
            topic_words = [topic.lower()]  # Fallback to full topic
        logger.info(f"Extracted keywords from topic '{topic}': {topic_words}")

        # Build query for exact or partial match on category titles
        query_conditions = ' || '.join([f'categories[]->title match "*{word}*"' for word in topic_words])
        query = f'*[_type == "post" && ({query_conditions})][0...{max_results}]{{title, "slug": slug.current, summary}}'
        if exclude_slug:
            query = f'*[_type == "post" && ({query_conditions}) && slug.current != "{exclude_slug}"][0...{max_results}]{{title, "slug": slug.current, summary}}'

        encoded_query = urllib.parse.quote(query, safe='')
        endpoint = f"/data/query/{self.dataset}?query={encoded_query}"
        attempt = 0
        results = []

        while attempt < max_retries:
            try:
                response = self._make_request("GET", endpoint)
                response.raise_for_status()
                results = response.json().get("result", [])
                if results:
                    logger.info(f"Fetched {len(results)} internal links for keywords {topic_words}.")
                    break
            except Exception as e:
                logger.warning(f"Query failed for keywords {topic_words} (attempt {attempt + 1}): {e}")
                attempt += 1
                if attempt < max_retries:
                    time.sleep(2 ** attempt)

        # Fallback: broader query using first keyword
        if not results:
            logger.info(f"No matches for keywords {topic_words}. Trying broader query.")
            broad_query = f'*[_type == "post" && categories[]->title match "*{topic_words[0]}*"][0...{max_results}]{{title, "slug": slug.current, summary}}'
            if exclude_slug:
                broad_query = f'*[_type == "post" && categories[]->title match "*{topic_words[0]}*" && slug.current != "{exclude_slug}"][0...{max_results}]{{title, "slug": slug.current, summary}}'
            encoded_broad_query = urllib.parse.quote(broad_query, safe='')
            broad_endpoint = f"/data/query/{self.dataset}?query={encoded_broad_query}"
            attempt = 0
            while attempt < max_retries:
                try:
                    response = self._make_request("GET", broad_endpoint)
                    response.raise_for_status()
                    results = response.json().get("result", [])
                    logger.info(f"Fetched {len(results)} internal links from broader query for '{topic_words[0]}'.")
                    break
                except Exception as e:
                    logger.warning(f"Broad query failed (attempt {attempt + 1}): {e}")
                    attempt += 1
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                    else:
                        logger.error(f"Failed to fetch internal links after {max_retries} attempts.")
                        return []

        # Validate relevance - but be less strict if we have results
        # Limit to max_results
        results = results[:max_results]
        validated_links = []
        for post in results:
            title = post.get("title", "").lower()
            slug = post.get("slug", "").lower()
            summary = post.get("summary", "")
            
            # Check if the post is relevant to any of the topic keywords
            is_relevant = any(word in title or word in slug for word in topic_words)
            
            # Include the post regardless of relevance (better to have some links than none)
            validated_links.append({"title": post["title"], "slug": f"https://owaisabdullah.dev/blog/{post['slug']}", "summary": summary})
            if not is_relevant:
                logger.info(f"Including potentially less relevant post: title='{post['title']}', slug='{post['slug']}'")
        
        logger.info(f"Validated {len(validated_links)} internal links for topic '{topic}'.")
        return validated_links



    def _make_request(self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None, max_retries: int = 3) -> requests.Response:
        """Makes an HTTP request with retry logic."""
        url = f"{self.base_url}{endpoint}"
        attempt = 0
        while attempt < max_retries:
            try:
                print(f"Making {method} request to {url} (Attempt {attempt + 1})")
                if data:
                    print(f"Request  {json.dumps(data, indent=2, default=str)}")
                response = requests.request(method, url, headers=self.headers, json=data, timeout=30)
                print(f"Response Status: {response.status_code}")
                return response
            except requests.exceptions.RequestException as e:
                attempt += 1
                logger.warning(f"Request failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt) # Exponential backoff
                else:
                    logger.error(f"Request failed after {max_retries} attempts.")
                    raise e

    def ensure_document_exists(self, doc_type: str, doc_id: str, fields: Optional[Dict[str, Any]] = None, max_retries: int = 3) -> bool:
        """
        Checks if a document exists, and creates it with default fields if it doesn't.
        """
        # --- FIX 1: Correctly build URL with query parameters ---
        query = f'*[_type == "{doc_type}" && _id == "{doc_id}"]'
        # Encode the query parameter properly for the URL
        encoded_query = urllib.parse.quote(query, safe='') 
        query_url_with_params = f"/data/query/{self.dataset}?query={encoded_query}"
        # --- END FIX 1 ---

        try:
            # Use the corrected URL
            response = self._make_request("GET", query_url_with_params, data=None, max_retries=max_retries)
            response.raise_for_status()
            result = response.json()
            if result.get("result"):
                logger.info(f"Document {doc_type} with ID {doc_id} already exists.")
                return True
        except Exception as e:
            logger.warning(f"Error checking if {doc_type} {doc_id} exists: {e}. Proceeding to create.")

        # Document doesn't exist or check failed, try to create
        mutate_url = f"{self.base_url}/data/mutate/{self.dataset}"
        default_doc = {"_type": doc_type, "_id": doc_id}
        if fields:
            default_doc.update(fields)
        payload = {"mutations": [{"createIfNotExists": default_doc}]}

        try:
            response = self._make_request("POST", f"/data/mutate/{self.dataset}", data=payload, max_retries=max_retries)
            response.raise_for_status()
            logger.info(f"Ensured document {doc_type} with ID {doc_id} exists.")
            return True
        except Exception as e:
             logger.error(f"Failed to ensure document {doc_type} {doc_id} exists: {e}")
             return False # Indicate failure


    def resolve_categories_to_refs(self, category_names: List[str], max_retries: int = 3) -> List[Dict[str, str]]:
        """
        Resolves a list of category names/slugs to Sanity references.
        Assumes category documents exist with _type 'category' and slugs matching the name (slugified).
        """
        category_refs = []
        if not category_names:
            return category_refs

        from slugify import slugify # Ensure slugify is available

        # Build query for multiple slugs
        slug_conditions = ' || '.join([f'slug.current == "{slugify(name)}"' for name in category_names])
        query = f'*[_type == "category" && ({slug_conditions})][0...{len(category_names)}]{{_id, slug}}'
        
        # --- FIX 2: Correctly build URL with query parameters ---
        encoded_query = urllib.parse.quote(query, safe='') 
        query_url_with_params = f"/data/query/{self.dataset}?query={encoded_query}"
        # --- END FIX 2 ---

        try:
            # Use the corrected URL
            response = self._make_request("GET", query_url_with_params, data=None, max_retries=max_retries)
            response.raise_for_status()
            results = response.json().get("result", [])
            found_slugs = {}
            for cat_doc in results:
                if 'slug' in cat_doc and 'current' in cat_doc['slug']:
                     found_slugs[cat_doc['slug']['current']] = cat_doc['_id']

            # Match input names to found IDs
            for name in category_names:
                slug = slugify(name)
                cat_id = found_slugs.get(slug)
                if cat_id:
                    category_refs.append({
                        "_key": str(uuid.uuid4()),  # This is the fix
                        "_type": "reference",
                        "_ref": cat_id
                    })
                else:
                    logger.warning(f"Category with slug '{slug}' (from name '{name}') not found in Sanity. Skipping reference.")
            return category_refs
        except Exception as e:
             logger.error(f"Error resolving category references: {e}")
             return []


    # Inside your SanityAdapter class

    def upload_image(self, image_path: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        Uploads an image to Sanity's asset store using the v1 API endpoint.
        Based on the confirmed working snippet.
        """
        # Normalize the path to handle different path separators
        normalized_path = os.path.normpath(image_path) if image_path else None
        logger.info(f"[SanityAdapter.upload_image] Attempting to upload image from path: {normalized_path}")
        logger.info(f"[SanityAdapter.upload_image] Current working directory: {os.getcwd()}")
        logger.info(f"[SanityAdapter.upload_image] File exists check: {os.path.exists(normalized_path) if normalized_path else False}")
        
        # Check if file exists
        if not os.path.exists(normalized_path):
            error_msg = f"Image file not found at path: {normalized_path}. Current working directory: {os.getcwd()}"
            logger.error(f"[SanityAdapter.upload_image] {error_msg}")
            return {"success": False, "error": error_msg}
        
        try:
            # Basic validation (from working snippet)
            from PIL import Image
            img = Image.open(normalized_path)
            img.verify() # Verifies it's a valid image file
            img_size = os.path.getsize(normalized_path)
            logger.info(f"[SanityAdapter.upload_image] Validated image file: {normalized_path}, Size: {img_size} bytes")
        except Exception as e:
            error_msg = f"Invalid image file '{normalized_path}': {str(e)}"
            logger.error(f"[SanityAdapter.upload_image] {error_msg}")
            return {"success": False, "error": error_msg}
        
        # Update the path to use the normalized version
        image_path = normalized_path

        # Construct URL with filename (from working snippet)
        filename_encoded = urllib.parse.quote(os.path.basename(image_path))
        upload_url = f"https://{self.project_id}.api.sanity.io/v1/assets/images/{self.dataset}?filename={filename_encoded}"

        # Guess MIME type (from working snippet)
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type:
            mime_type = "image/jpeg" # Default fallback (from working snippet)
        logger.debug(f"[SanityAdapter.upload_image] Guessed MIME type: {mime_type}")

        # Prepare headers (from working snippet)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": mime_type # Explicitly set Content-Type
        }

        attempt = 0
        while attempt < max_retries:
            try:
                # Read image data (implicitly done in working snippet by reading file for requests.post 'data' param)
                with open(image_path, 'rb') as f:
                    image_data = f.read() # Read raw bytes

                logger.debug(f"[SanityAdapter.upload_image] Attempt {attempt + 1} - Upload URL: {upload_url}")
                logger.debug(f"[SanityAdapter.upload_image] Attempt {attempt + 1} - Headers: {headers}")
                logger.debug(f"[SanityAdapter.upload_image] Attempt {attempt + 1} - Data size: {len(image_data)} bytes")
                # Log first few bytes to verify
                logger.debug(f"[SanityAdapter.upload_image] Attempt {attempt + 1} - First 20 bytes (hex): {image_data[:20].hex()}")

                # Make request (from working snippet logic)
                logger.info(f"[SanityAdapter.upload_image] Uploading image (raw data): {image_path} (Attempt {attempt + 1})")
                response = requests.post(upload_url, headers=headers, data=image_data, timeout=60)

                logger.info(f"[SanityAdapter.upload_image] Upload response status: {response.status_code}")

                if response.status_code in [200, 201]:
                    try:
                        result = response.json()
                        asset_document = result.get('document')
                        if asset_document and '_id' in asset_document:
                            asset_id = asset_document['_id']
                            logger.info(f"[SanityAdapter.upload_image] Image uploaded successfully. Asset ID: {asset_id}")
                            return {
                                "success": True,
                                "asset_id": asset_id,
                                "url": asset_document.get('url'),
                                "original_filename": asset_document.get('originalFilename')
                            }
                        else:
                            error_msg = f"Upload successful but unexpected response structure: {result}"
                            logger.error(f"[SanityAdapter.upload_image] {error_msg}")
                            return {"success": False, "error": error_msg}
                    except ValueError as ve: # JSON decode error
                        error_msg = f"Failed to decode JSON response: {ve}. Response text: {response.text[:200]}..."
                        logger.error(f"[SanityAdapter.upload_image] {error_msg}")
                        return {"success": False, "error": error_msg}
                else:
                    # Handle non-2xx status codes
                    error_text = response.text
                    try:
                        error_json = response.json()
                        error_text = error_json.get('message', error_text)
                    except:
                        pass # Use raw response text if JSON parsing fails
                    error_msg = f"Image upload failed (Status {response.status_code}): {error_text}"
                    logger.error(f"[SanityAdapter.upload_image] {error_msg}")
                    # Don't retry on client errors (4xx)
                    if 400 <= response.status_code < 500:
                        return {"success": False, "error": error_msg}

            except FileNotFoundError:
                error_msg = f"Image file not found: {image_path}"
                logger.error(f"[SanityAdapter.upload_image] {error_msg}")
                return {"success": False, "error": error_msg}
            except requests.exceptions.RequestException as e:
                # Network errors, timeouts, etc.
                error_msg = f"Network error during image upload (Attempt {attempt + 1}): {e}"
                logger.warning(f"[SanityAdapter.upload_image] {error_msg}")
            except Exception as e:
                # Catch other unexpected errors during upload attempt
                error_msg = f"Unexpected error during image upload (Attempt {attempt + 1}): {e}"
                logger.error(f"[SanityAdapter.upload_image] {error_msg}", exc_info=True)

            # If we reach here, the attempt failed
            attempt += 1
            if attempt < max_retries:
                wait_time = 2 ** attempt # Exponential backoff
                logger.info(f"[SanityAdapter.upload_image] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                final_error_msg = f"Image upload failed after {max_retries} attempts."
                logger.error(f"[SanityAdapter.upload_image] {final_error_msg}")
                return {"success": False, "error": final_error_msg}

        # This line should not be reached due to the return in the loop, but added for safety
        return {"success": False, "error": "Max retries exceeded in upload_image (Unexpected flow)"}


    # Inside your SanityAdapter class

    def create_document(self, document: Dict[str, Any], max_retries: int = 3) -> Dict[str, Any]:
        """
        Creates a document in Sanity using the older Mutations API.
        Improved ID extraction logic.
        """
        endpoint = f"/data/mutate/{self.dataset}"
        payload = {
            "mutations": [
                {
                    "create": document
                }
            ]
        }

        attempt = 0
        while attempt < max_retries:
            try:
                full_url = f"{self.base_url}{endpoint}"
                logger.debug(f"[SanityAdapter.create_document] Using Mutations API")
                logger.debug(f"[SanityAdapter.create_document] Constructed full URL: {full_url}")
                logger.info(f"[SanityAdapter.create_document] Attempt {attempt + 1}: Making POST request")
                logger.debug(f"[SanityAdapter.create_document] URL: {full_url}")
                logger.debug(f"[SanityAdapter.create_document] Headers: {self.headers}")
                logger.debug(f"[SanityAdapter.create_document] Payload: {json.dumps(payload, indent=2)}")

                response = self._make_request("POST", endpoint, data=payload, max_retries=1)
                response.raise_for_status()

                result_data = response.json()
                logger.debug(f"[SanityAdapter.create_document] Response JSON: {json.dumps(result_data, indent=2)}")

                # --- Improved ID Extraction Logic ---
                document_id = None

                # Method 1: Check if ID is directly in the response body (common for auto-generated IDs)
                if "documentId" in result_data:
                    document_id = result_data["documentId"]
                    logger.debug(f"[SanityAdapter.create_document] Found document ID in 'documentId': {document_id}")

                # Method 2: Check the results array (standard location)
                if not document_id:
                    results = result_data.get("results", [])
                    if results:
                        # The ID might be in the first result, or we might need to iterate
                        first_result = results[0] if len(results) > 0 else {}
                        # Check if 'id' is directly in the result item (standard)
                        if "id" in first_result:
                            document_id = first_result["id"]
                            logger.debug(f"[SanityAdapter.create_document] Found document ID in 'results[0].id': {document_id}")
                        # Check if 'id' is nested under a 'document' key in the result (less common, but possible in some ops)
                        elif "document" in first_result and isinstance(first_result["document"], dict) and "_id" in first_result["document"]:
                            document_id = first_result["document"]["_id"]
                            logger.debug(f"[SanityAdapter.create_document] Found document ID in 'results[0].document._id': {document_id}")

                # Method 3: If the document being created has an _id specified client-side,
                # the API might echo it back or confirm creation without a new ID in results.
                # This is less relevant if Sanity auto-generates the ID.

                # --- Determine Success ---
                if document_id:
                    # If we found an ID, consider it a success
                    logger.info(f"[SanityAdapter.create_document] Document created successfully with ID: {document_id}")
                    return {
                        "success": True,
                        "document_id": document_id,
                        "results": result_data # Return full response for inspection
                    }
                else:
                    # If no ID was found, it's ambiguous. The 200 status means the request was processed,
                    # but maybe the document already existed (if using 'create' and it fails silently?)
                    # or the ID isn't returned in the expected places.
                    # Let's check for other success indicators or errors in the response.
                    # Sanity usually returns an error in the body if the mutation failed, even with 200.
                    # If we got here and results exist, it might be a success without an easy-to-find ID.
                    # However, the most likely case is we just missed the ID.

                    # Log a more detailed warning about the response structure
                    logger.warning(
                        f"[SanityAdapter.create_document] Mutation returned 200 OK, but document ID could not be extracted. "
                        f"Response keys: {list(result_data.keys())}. "
                        f"Results (if any): {result_data.get('results', 'N/A')}. "
                        f"Full response data keys sample: {list(result_data.keys())[:10] if isinstance(result_data, dict) else 'N/A'}"
                    )
                    # Assume success if status 200 and no explicit error, even if ID parsing failed.
                    # This is risky, but returning an error here prevents successful posts.
                    # A better way is to ensure ID is always returned or query for it.
                    # For now, let's assume success based on 200 OK and no error in response.
                    # Check for explicit error in response body
                    if "error" in result_data:
                        error_details = result_data["error"]
                        logger.error(f"[SanityAdapter.create_document] API returned 200 but body contains error: {error_details}")
                        return {"success": False, "error": f"API error in response body: {error_details}"}

                    logger.info("[SanityAdapter.create_document] Assuming success based on 200 OK status and no ID parsing error or explicit API error.")
                    return {
                        "success": True,
                        "document_id": "unknown_but_likely_created", # Indicate ID parsing issue
                        "message": "Document creation requested successfully (200 OK), but ID could not be extracted from response. Check Sanity Studio.",
                        "results": result_data
                    }

                # --- End Improved ID Extraction Logic ---


            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP error during document creation: {e}"
                try:
                    error_details = e.response.json()
                    error_msg = f"Document creation failed (HTTP {e.response.status_code}): {error_details.get('message', str(error_details))}"
                except:
                    pass
                logger.error(f"[SanityAdapter.create_document] {error_msg}")
                return {"success": False, "error": error_msg}
            except Exception as e:
                error_msg = f"Unexpected error during document creation (Attempt {attempt + 1}): {e}"
                logger.error(f"[SanityAdapter.create_document] {error_msg}", exc_info=True)

            attempt += 1
            if attempt < max_retries:
                wait_time = 2 ** attempt
                logger.info(f"[SanityAdapter.create_document] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                final_error_msg = f"Document creation failed after {max_retries} attempts."
                logger.error(f"[SanityAdapter.create_document] {final_error_msg}")
                return {"success": False, "error": final_error_msg}

        return {"success": False, "error": "Max retries exceeded in create_document (Unexpected flow)"}



    def post_blog(self, title: str, summary: str, content: str, categories: List[str],
                  local_image_path: str, slug: str, alt_text: str, faqs: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Posts a blog post to Sanity CMS, correctly mapping fields to the 'post' schema, including FAQs.
        Uses the markdown_to_sanity_blocks parser for content.
        """
        from slugify import slugify

        print(f"DEBUG: Converting content of length {len(content)}")
        print(f"DEBUG: First 100 chars: {content[:100]}")
        
        try:
            # 1. Use existing author from your project (configured via environment variables)
            author_id = os.environ.get("SANITY_DEFAULT_AUTHOR_ID", "default-author-id")
            author_name = os.environ.get("SANITY_DEFAULT_AUTHOR_NAME", "Admin")
            # Ensure the author exists (this will just verify it exists)
            self.ensure_document_exists("author", author_id, {"name": author_name})

            # 2. Upload Image - check if it's a URL or local path
            # Determine if image_path is a URL from Freepik/Pexel (no need to download) or local file
            image_asset_id = None
            image_url = None
            
            if local_image_path and local_image_path.startswith('http'):
                # It's a URL - check if it's from Freepik or Pexel to use directly
                freepik_url = 'freepik' in local_image_path.lower()
                pexel_url = 'pexels' in local_image_path.lower()
                
                if freepik_url or pexel_url:
                    # For Freepik or Pexel URLs, we'll let the main document creation process handle the URL
                    # since Sanity can handle direct external image URLs in the content
                    logger.info(f"Detected {('Freepik' if freepik_url else 'Pexel')} URL, will handle in main document: {local_image_path}")
                    # To use direct URLs, we'll need to pass the URL directly as the image_asset_id
                    # But since Sanity's image asset references need a proper asset ID, we still need to upload
                    # However, we can try to handle this by using Sanity's asset upload directly from URL
                    try:
                        # Use Sanity's direct asset upload from URL functionality
                        import tempfile
                        import requests
                        from urllib.parse import urlparse
                        
                        response = requests.get(local_image_path)
                        response.raise_for_status()
                        
                        # Clean the image URL by removing query parameters to avoid file system issues
                        parsed_url = urlparse(local_image_path)
                        clean_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
                        
                        # Get file extension from cleaned URL or default to .jpg
                        ext = os.path.splitext(parsed_url.path)[1]
                        if not ext:
                            content_type = response.headers.get('content-type', 'image/jpeg')
                            if 'png' in content_type:
                                ext = '.png'
                            elif 'gif' in content_type:
                                ext = '.gif'
                            else:
                                ext = '.jpg'
                        
                        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                            tmp.write(response.content)
                            temp_image_path = tmp.name
                        
                        # Upload to Sanity using the temporary file
                        image_upload_result = self.upload_image(temp_image_path)
                        os.unlink(temp_image_path)  # Clean up temp file
                        
                        if image_upload_result.get("success"):
                            image_asset_id = image_upload_result["asset_id"]
                            image_url = image_upload_result.get("url")
                        else:
                            return {
                                "status": "error",
                                "post_id": None,
                                "image_id": None,
                                "image_url": None,
                                "error": f"Failed to upload image from URL: {image_upload_result.get('error')}"
                            }
                    except Exception as e:
                        return {
                            "status": "error",
                            "post_id": None,
                            "image_id": None,
                            "image_url": None,
                            "error": f"Failed to handle image URL: {str(e)}"
                        }
                else:
                    # For other URLs (like HuggingFace temporary files), download and upload to Sanity
                    try:
                        import tempfile
                        import requests
                        response = requests.get(local_image_path)
                        response.raise_for_status()
                        
                        # Get file extension from URL or default to .jpg
                        ext = os.path.splitext(local_image_path)[1]
                        if not ext:
                            content_type = response.headers.get('content-type', 'image/jpeg')
                            if 'png' in content_type:
                                ext = '.png'
                            elif 'gif' in content_type:
                                ext = '.gif'
                            else:
                                ext = '.jpg'
                        
                        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                            tmp.write(response.content)
                            temp_image_path = tmp.name
                        
                        # Upload to Sanity
                        image_upload_result = self.upload_image(temp_image_path)
                        os.unlink(temp_image_path)  # Clean up temp file
                        
                        if image_upload_result.get("success"):
                            image_asset_id = image_upload_result["asset_id"]
                            image_url = image_upload_result.get("url")
                        else:
                            return {
                                "status": "error",
                                "post_id": None,
                                "image_id": None,
                                "image_url": None,
                                "error": f"Failed to upload image from URL: {image_upload_result.get('error')}"                           }
                    except Exception as e:
                        return {
                            "status": "error",
                            "post_id": None,
                            "image_id": None,
                            "image_url": None,
                            "error": f"Failed to handle image URL: {str(e)}"
                        }
            else:
                # It's a local file path
                # Normalize the image path
                normalized_image_path = os.path.normpath(local_image_path) if local_image_path else None
                
                # Check if file exists
                if not os.path.exists(normalized_image_path):
                    return {
                        "status": "error",
                        "post_id": None,
                        "image_id": None,
                        "image_url": None,
                        "error": f"Image file not found at path: {normalized_image_path}. Current working directory: {os.getcwd()}"
                    }
                
                # Use the normalized path
                image_upload_result = self.upload_image(normalized_image_path)
                if not image_upload_result.get("success"):
                    return {
                        "status": "error",
                        "post_id": None,
                        "image_id": None,
                        "image_url": None,
                        "error": f"Failed to upload image: {image_upload_result.get('error')}"
                    }

                image_asset_id = image_upload_result["asset_id"]
                image_url = image_upload_result.get("url")

            # 3. Prepare Document Content
            try:
                cleaned_content = '\n'.join([line.lstrip() for line in content.split('\n')]).strip() if content else ""
                logger.debug(f"Original content first 100 chars: {content[:100] if content else 'empty'}")
                logger.debug(f"Cleaned content first 100 chars: {cleaned_content[:100] if cleaned_content else 'empty'}")
                
                content_blocks = markdown_to_sanity_blocks(cleaned_content, debug=True)
                
                if not content_blocks:
                    logger.warning("Markdown parser returned no blocks for content.")
                    content_blocks = [{
                        "_key": str(uuid.uuid4()),
                        "_type": "block",
                        "children": [{
                            "_key": str(uuid.uuid4()),
                            "_type": "span",
                            "text": "No content generated or parsing resulted in empty blocks"
                        }],
                        "markDefs": [],
                        "style": "normal"
                    }]
                    
            except Exception as parse_error:
                error_msg = f"Error converting Markdown to Sanity blocks: {parse_error}"
                logger.error(error_msg, exc_info=True)
                fallback_content = content.strip() if content else ""
                content_blocks = [
                    {
                        "_key": str(uuid.uuid4()),
                        "_type": "block",
                        "children": [
                            {
                                "_key": str(uuid.uuid4()),
                                "_type": "span",
                                "text": f"[Content Conversion Error: {str(parse_error)}]",
                                "marks": ["strong"]
                            }
                        ],
                        "markDefs": [],
                        "style": "normal"
                    },
                    {
                        "_key": str(uuid.uuid4()),
                        "_type": "block",
                        "children": [
                            {
                                "_key": str(uuid.uuid4()),
                                "_type": "span",
                                "text": fallback_content
                            }
                        ],
                        "markDefs": [],
                        "style": "normal"
                    }
                ]

            # 4. Process content blocks - upload images and replace URLs with asset references
            processed_blocks = []
            for block in content_blocks:
                if block.get("_type") == "image":
                    # Handle embedded images in content
                    image_url = block.get("asset", {}).get("url")
                    if image_url:
                        logger.info(f"[SanityAdapter.post_blog] Processing embedded image: {image_url}")
                        try:
                            # Download and upload the image to Sanity
                            if image_url.startswith("http"):
                                # Remote image - download first
                                # Clean the image URL by removing query parameters to avoid file system issues
                                clean_image_url = image_url.split('?')[0]
                                import tempfile
                                import requests
                                response = requests.get(clean_image_url)
                                response.raise_for_status()
                                
                                # Get file extension from cleaned URL or content type
                                ext = os.path.splitext(clean_image_url)[1] or ".jpg"
                                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                    tmp.write(response.content)
                                    temp_image_path = tmp.name
                                
                                logger.info(f"[SanityAdapter.post_blog] Downloaded image from {clean_image_url} to: {temp_image_path}")
                                
                                # Upload to Sanity
                                upload_result = self.upload_image(temp_image_path)
                                os.unlink(temp_image_path)  # Clean up temp file
                                
                                if upload_result.get("success"):
                                    logger.info(f"[SanityAdapter.post_blog] Successfully uploaded image: {upload_result}")
                                    # Replace the image block with a proper Sanity image reference
                                    new_block = {
                                        "_key": block.get("_key", str(uuid.uuid4())),
                                        "_type": "image",
                                        "asset": {
                                            "_type": "reference",
                                            "_ref": upload_result["asset_id"]
                                        }
                                    }
                                    # Preserve alt text and title if they exist
                                    if "alt" in block:
                                        new_block["alt"] = block["alt"]
                                    if "title" in block:
                                        new_block["title"] = block["title"]
                                    processed_blocks.append(new_block)
                                else:
                                    # If upload fails, keep the original block or create a placeholder
                                    logger.warning(f"[SanityAdapter.post_blog] Failed to upload embedded image: {upload_result.get('error')}")
                                    # Create a text block as fallback with more descriptive error
                                    alt_text = block.get("alt", "Embedded image")
                                    fallback_block = {
                                        "_key": str(uuid.uuid4()),
                                        "_type": "block",
                                        "children": [
                                            {
                                                "_key": str(uuid.uuid4()),
                                                "_type": "span",
                                                "text": f"[Image: {alt_text} - Upload failed: {upload_result.get('error', 'Unknown error')}]" 
                                            }
                                        ],
                                        "markDefs": [],
                                        "style": "normal"
                                    }
                                    processed_blocks.append(fallback_block)
                            else:
                                # Local image - upload directly
                                # Check if the file exists first
                                if os.path.exists(image_url):
                                    upload_result = self.upload_image(image_url)
                                    if upload_result.get("success"):
                                        # Replace the image block with a proper Sanity image reference
                                        new_block = {
                                            "_key": block.get("_key", str(uuid.uuid4())),
                                            "_type": "image",
                                            "asset": {
                                                "_type": "reference",
                                                "_ref": upload_result["asset_id"]
                                            }
                                        }
                                        # Preserve alt text and title if they exist
                                        if "alt" in block:
                                            new_block["alt"] = block["alt"]
                                        if "title" in block:
                                            new_block["title"] = block["title"]
                                        processed_blocks.append(new_block)
                                    else:
                                        # If upload fails, keep the original block or create a placeholder
                                        logger.warning(f"Failed to upload embedded image: {upload_result.get('error')}")
                                        # Create a text block as fallback
                                        alt_text = block.get("alt", "Embedded image")
                                        fallback_block = {
                                            "_key": str(uuid.uuid4()),
                                            "_type": "block",
                                            "children": [
                                                {
                                                    "_key": str(uuid.uuid4()),
                                                    "_type": "span",
                                                    "text": f"[Image: {alt_text} - Upload failed: {upload_result.get('error', 'Unknown error')}]" 
                                                }
                                            ],
                                            "markDefs": [],
                                            "style": "normal"
                                        }
                                        processed_blocks.append(fallback_block)
                                else:
                                    logger.warning(f"Local image file does not exist: {image_url}")
                                    # Create a text block as fallback
                                    alt_text = block.get("alt", "Embedded image")
                                    fallback_block = {
                                        "_key": str(uuid.uuid4()),
                                        "_type": "block",
                                        "children": [
                                            {
                                                "_key": str(uuid.uuid4()),
                                                "_type": "span",
                                                "text": f"[Image: {alt_text} - File not found: {image_url}]" 
                                            }
                                        ],
                                        "markDefs": [],
                                        "style": "normal"
                                    }
                                    processed_blocks.append(fallback_block)
                        except Exception as e:
                            logger.error(f"Error processing embedded image: {e}", exc_info=True)
                            # Create a text block as fallback
                            alt_text = block.get("alt", "Embedded image")
                            fallback_block = {
                                "_key": str(uuid.uuid4()),
                                "_type": "block",
                                "children": [
                                    {
                                        "_key": str(uuid.uuid4()),
                                        "_type": "span",
                                        "text": f"[Image: {alt_text} - Processing error: {str(e)}]" 
                                    }
                                ],
                                "markDefs": [],
                                "style": "normal"
                            }
                            processed_blocks.append(fallback_block)
                    else:
                        # No URL, keep the block as is
                        processed_blocks.append(block)
                elif block.get("_type") == "block":
                    # Process text blocks to make sure inline images in markdown are handled properly
                    # (though our markdown parser should prevent inline images in text blocks)
                    processed_blocks.append(block)
                else:
                    # Other types of blocks, keep as is
                    processed_blocks.append(block)

            # Update content_blocks with processed blocks
            content_blocks = processed_blocks

            # 5. Prepare FAQs
            formatted_faqs = []
            if faqs: # Check if faqs list is provided and not empty
                for faq_item in faqs:
                    # Check if faq_item is a dict with the required keys
                    if isinstance(faq_item, dict) and "question" in faq_item and "answer" in faq_item:
                        # Wrap the FAQ item dict with _type: "object" as required by the schema
                        formatted_faq = {
                            "_key": str(uuid.uuid4()), 
                            "_type": "object", 
                            "question": faq_item["question"],
                            "answer": faq_item["answer"]
                        }
                        formatted_faqs.append(formatted_faq)
                    else:
                        logger.warning(f"Skipping invalid FAQ item: {faq_item}")
            if not formatted_faqs:
                logger.warning("No valid FAQs provided; setting empty array.")
                formatted_faqs = []

            # 6. Resolve Categories
            category_refs = self.resolve_categories_to_refs(categories)
            if not category_refs and categories:
                logger.warning("Failed to resolve any category references.")

            # 7. Construct Document Object
            full_url = f"https://owaisabdullah.dev/blog/{slug}"

            document = {
                "_type": "post",
                "title": title,
                "summary": summary,
                "slug": {"_type": "slug", "current": slug},
                "author": {"_type": "reference", "_ref": author_id},
                "mainImage": {
                    "_type": "image",
                    "asset": {"_type": "reference", "_ref": image_asset_id},
                    "alt": alt_text or f"Image for {title}"
                },
                "categories": category_refs,
                "content": content_blocks,
                "faqs": formatted_faqs
            }

            # 8. Create Document
            create_result = self.create_document(document)

            # Inside SanityAdapter.post_blog, after create_result = self.create_document(...)
            logger.debug(f"[SanityAdapter.post_blog] create_document returned: {create_result}")
            if not create_result.get("success"):
                logger.warning(f"[SanityAdapter.post_blog] create_document reported failure: {create_result.get('error')}")
                # ... handle error ...
            else:
                doc_id = create_result.get("document_id")
                logger.info(f"[SanityAdapter.post_blog] create_document reported success. Document ID: {doc_id}")
                if not doc_id or doc_id.startswith("unknown"): # Check for ambiguous success
                    logger.error(f"[SanityAdapter.post_blog] create_document success, but ID is invalid/unreliable: {doc_id}")
                    # Maybe return an error status here?
                    # return {"status": "error", "error": "Document ID could not be confirmed after creation."}

            # 9. Handle Result
            if not create_result.get("success"):
                return {
                    "status": "error",
                    "post_id": None,
                    "image_id": image_asset_id,
                    "image_url": image_url,
                    "post_url": full_url,
                    "error": f"Failed to create document in Sanity: {create_result.get('error')}"
                }

            post_id = create_result.get("document_id")
            if not post_id:
                logger.error(f"Document creation reported success but missing document ID. Full result: {create_result}")
                return {
                    "status": "error",
                    "post_id": None,
                    "image_id": image_asset_id,
                    "image_url": image_url,
                    "post_url": full_url,
                    "error": "Document creation succeeded but failed to retrieve document ID from response."
                }

            logger.info(f"Blog post '{title}' created successfully with ID: {post_id}")
            return {
                "status": "success",
                "post_id": post_id,
                "image_id": image_asset_id,
                "image_url": image_url,
                "post_url": full_url,
                "message": f"Blog post created successfully with ID {post_id}."
            }

        except Exception as e:
            logger.error(f"Unexpected error in post_blog: {e}", exc_info=True)
            return {
                "status": "error",
                "post_id": None,
                "image_id": None,
                "image_url": None,
                "post_url": full_url,
                "error": f"An unexpected error occurred: {str(e)}"
            }