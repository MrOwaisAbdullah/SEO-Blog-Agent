import re
import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

RAPID_API_YOUTUBE_TRANSCRIPT_API_KEY = os.getenv("RAPID_API_YOUTUBE_TRANSCRIPT_API_KEY")
YOUTUBE_TRANSCRIPT_IO_API_TOKEN_2 = os.getenv("YOUTUBE_TRANSCRIPT_IO_API_TOKEN_2")
YOUTUBE_TRANSCRIPT_IO_API_TOKEN = os.getenv("YOUTUBE_TRANSCRIPT_IO_API_TOKEN")

def get_youtube_transcript(url_or_id):
    """
    Fetches the transcript of a YouTube video using multiple fallback APIs.

    Args:
        url_or_id (str): The YouTube video URL or video ID.

    Returns:
        dict or None: The full transcript data if successful, None otherwise.
        The structure will vary depending on the API used.
    """

    # Extract video ID from URL if necessary
    # Using regex pattern to match various YouTube URL formats
    video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url_or_id)
    video_id = video_id_match.group(1) if video_id_match else url_or_id

    if len(video_id) != 11:
        print("Invalid YouTube video ID or URL.")
        return None

    # List of API functions to try in order
    api_functions = []

    # Add youtube-transcript.io as first option if token is provided
    if YOUTUBE_TRANSCRIPT_IO_API_TOKEN:
        api_functions.append(lambda vid: _fetch_transcript_youtube_transcript_io(vid, YOUTUBE_TRANSCRIPT_IO_API_TOKEN))

    # Add youtube-transcriptor.p.rapidapi.com as a fallback
    if RAPID_API_YOUTUBE_TRANSCRIPT_API_KEY:
        api_functions.append(_fetch_transcript_rapidapi)

    # Add youtube-transcript.io with second token as another fallback
    if YOUTUBE_TRANSCRIPT_IO_API_TOKEN_2:
        api_functions.append(lambda vid: _fetch_transcript_youtube_transcript_io(vid, YOUTUBE_TRANSCRIPT_IO_API_TOKEN_2))

    # Try each API function in order
    for api_func in api_functions:
        try:
            transcript = api_func(video_id)
            if transcript is not None:  # Check for valid response
                return transcript # Return the full data structure
        except Exception as e:
            print(f"API call failed: {e}")
            continue  # Try the next API

    print("All APIs failed to retrieve the transcript.")
    return {"error": "All APIs failed"}

def extract_transcript_text(raw_transcript_data, preferred_language='en'):
    """
    Extracts and concatenates the transcript text from the raw API response.

    Args:
        raw_transcript_data (dict or list): The raw data returned by get_youtube_transcript.
        preferred_language (str): The preferred language code (e.g., 'en', 'de').

    Returns:
        str or None: The concatenated transcript text, or None if extraction fails.
    """
    if not raw_transcript_data:
        return None

    transcript_text = ""
    data_to_process = raw_transcript_data

    # --- Handle RapidAPI response structure ---
    # The RapidAPI response is a LIST containing one DICT
    # e.g., [{ "title": "...", "description": "...", "transcription": [...] }]
    if isinstance(raw_transcript_data, list) and len(raw_transcript_data) > 0 and isinstance(raw_transcript_data[0], dict):
        # Process the first (and likely only) dictionary in the list
        data_to_process = raw_transcript_data[0]
    # If it's already a dict (e.g., from youtube-transcript.io), process it directly
    elif isinstance(raw_transcript_data, dict):
        data_to_process = raw_transcript_data
    else:
        # If it's neither a list nor a dict, we can't process it
        print("Warning: Raw data is neither a list nor a dictionary.")
        return None

    # --- Now process the 'data_to_process' dictionary ---

    # --- Handle response from youtube-transcript.io ---
    # Based on your previous example output structure
    if 'tracks' in data_to_process:
        # Find the track for the preferred language or the first available one
        selected_track = None
        for track in data_to_process.get('tracks', []):
            # Check for languageCode (from your example) or language
            if track.get('languageCode', '').lower() == preferred_language.lower() or \
               track.get('language', '').lower() == preferred_language.lower():
                selected_track = track
                break
        # Fallback to first track if preferred language not found
        if not selected_track and data_to_process.get('tracks'):
             selected_track = data_to_process['tracks'][0]

        if selected_track and 'transcript' in selected_track:
            # Concatenate the text segments (using 'text' key)
            transcript_text = " ".join([entry.get('text', '') for entry in selected_track['transcript']])
            return transcript_text.strip()

    # --- Handle response from youtube-transcriptor.p.rapidapi.com ---
    # Based on the structure in Pasted_Text_1753910310274.txt
    # After extracting the dict from the list, check for 'transcription'
    elif 'transcription' in data_to_process:
        # The transcript segments are under the 'transcription' key
        # Each segment seems to use 'subtitle' instead of 'text'
        transcription_segments = data_to_process.get('transcription', [])
        if isinstance(transcription_segments, list):
            transcript_text = " ".join([segment.get('subtitle', '') for segment in transcription_segments])
            return transcript_text.strip()

    # --- Handle other potential structures (less specific) ---
    # This part handles cases like a direct list of segments or dicts with 'segments'/'transcript' keys
    # It now operates on 'data_to_process' which could be the original dict or the one extracted from the list
    elif isinstance(data_to_process, list):
         # If 'data_to_process' itself is a list of segments (like [{'text': '...', 'start': X, 'duration': Y}, ...])
         # Try 'text' first, then 'subtitle'
         transcript_text = " ".join([entry.get('text', entry.get('subtitle', '')) for entry in data_to_process])
         return transcript_text.strip()

    elif isinstance(data_to_process, dict):
        # Check for 'segments' or 'transcript' key containing a list
        for key in ['segments', 'transcript']:
            if key in data_to_process and isinstance(data_to_process[key], list):
                # Try 'text' first, then 'subtitle'
                transcript_text = " ".join([entry.get('text', entry.get('subtitle', '')) for entry in data_to_process[key]])
                return transcript_text.strip()

        # If 'transcript' key holds the text directly (unlikely but possible)
        if 'transcript' in data_to_process and isinstance(data_to_process['transcript'], str):
              return data_to_process['transcript'].strip()


    # If structure is not recognized
    print("Warning: Could not extract transcript text from the provided data structure.")
    # Uncomment the line below if you want to see the structure that failed
    # print(f"Unrecognized structure keys: {list(data_to_process.keys()) if isinstance(data_to_process, dict) else 'Not a dict'}")
    return None # Return None to indicate failure to extract

def _fetch_transcript_youtube_transcript_io(video_id, api_token):
    """Fetches transcript using youtube-transcript.io API."""
    url = "https://www.youtube-transcript.io/api/transcripts"
    headers = {
        "Authorization": f"Basic {api_token}",
        "Content-Type": "application/json"
    }
    payload = {"ids": [video_id]}

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        data = response.json()
        # Assuming the API returns a list, and we want the transcript for our single ID
        # The exact structure depends on the API response format
        if isinstance(data, list) and len(data) > 0:
            return data[0] # Return the first (and likely only) item in the list
        elif isinstance(data, dict):
            return data
    elif response.status_code == 429:
        # Handle rate limiting
        retry_after = int(response.headers.get('Retry-After', 10))
        print(f"Rate limit hit. Waiting for {retry_after} seconds.")
        time.sleep(retry_after)
        # Retry the same API call once after waiting
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            elif isinstance(data, dict):
                return data
    else:
        print(f"youtube-transcript.io API error: {response.status_code} - {response.text}")

    return None

def _fetch_transcript_rapidapi(video_id):
    """Fetches transcript using youtube-transcriptor.p.rapidapi.com API."""
    url = "https://youtube-transcriptor.p.rapidapi.com/transcript"
    # Consider making 'lang' configurable if needed, defaulting to 'en'
    querystring = {"video_id": video_id, "lang": "en"}
    headers = {
        "x-rapidapi-key": RAPID_API_YOUTUBE_TRANSCRIPT_API_KEY,
        "x-rapidapi-host": "youtube-transcriptor.p.rapidapi.com"
    }

    response = requests.get(url, headers=headers, params=querystring)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"RapidAPI transcriptor error: {response.status_code} - {response.text}")
        return None

    

def transcriptor_tool(video_url_or_id, preferred_language='en', raw=False):
    """
    Main function to get the YouTube transcript using the best available API using the url or ID.

    Args:
        video_url_or_id (str): The YouTube video URL or ID.
        preferred_language (str): The preferred language code for the transcript, defaulting to 'en' English.
        raw (bool): If True, returns the full raw transcript data; if False, returns just the cleaned text, defaulting to False.

    Returns:
        dict: The full transcript data if successful, or an error message.
    """
    try:
        # First, try the youtube-transcript-api
        raw_transcript = get_youtube_transcript(video_url_or_id)

        # 2. Extract just the text
        clean_transcript_text = extract_transcript_text(raw_transcript, preferred_language=preferred_language)
        if raw:
            return {"raw_transcript": raw_transcript}
        else:
            return {"clean_transcript": clean_transcript_text}
    except Exception as e:
        print(f"Could not retrieve transcript for the given video: {e}")

    return {"error": "All APIs failed"}



# --- Example Usage ---
# (Only runs if this script is executed directly)
if __name__ == "__main__":
    # Example with the video ID from your file content
    video_url_or_id = "https://www.youtube.com/shorts/jo6U429l3JM?feature=share" # Example ID from Pasted_Text_1753910310274.txt

    # 1. Get the full raw transcript data
    raw_transcript = get_youtube_transcript(video_url_or_id)

    if raw_transcript:
        print("--- Raw Transcript Data (First 500 chars) ---")
        raw_str = str(raw_transcript)
        print(raw_str[:500] + ("..." if len(raw_str) > 500 else ""))
        print("-" * 20)

        # 2. Extract just the text
        clean_transcript_text = extract_transcript_text(raw_transcript, preferred_language='en')

        if clean_transcript_text:
            print("--- Extracted Transcript Text ---")
            print(clean_transcript_text)
            print("-" * 20)
            # Now you can feed `clean_transcript_text` to your AI agent
            # Example: agent.run(f"Summarize this video transcript: {clean_transcript_text}")

        else:
            print("Failed to extract clean text from the transcript data.")
    else:
        print("Could not retrieve transcript for the given video.")
