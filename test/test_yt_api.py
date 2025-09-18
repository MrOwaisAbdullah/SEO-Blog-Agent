# test_yt_api.py
from youtube_transcript_api import YouTubeTranscriptApi

def test_basic_functionality():
    print("Testing youtube-transcript-api...")
    try:
        # List method
        transcript_list = YouTubeTranscriptApi.list_transcripts('9bZkp7q19f0') # Gangnam Style
        print("list_transcripts method: SUCCESS")
        print(f"  Available transcripts: {list(transcript_list)}") # Print available transcripts
    except Exception as e:
        print(f"list_transcripts method: FAILED - {e}")

    try:
        # Get method (primary one we want to use)
        transcript_data = YouTubeTranscriptApi.get_transcript('9bZkp7q19f0', languages=['en'])
        print("get_transcript method: SUCCESS")
        print(f"  First 100 chars of transcript: {transcript_data[0]['text'][:100] if transcript_data else 'N/A'}")
        print(f"  Number of segments: {len(transcript_data) if transcript_data else 'N/A'}")
    except Exception as e:
         print(f"get_transcript method: FAILED - {e}")

    # Print attributes of the class to see what's actually there
    print("\nAttributes of YouTubeTranscriptApi class:")
    print([attr for attr in dir(YouTubeTranscriptApi) if not attr.startswith('_')])


if __name__ == "__main__":
    test_basic_functionality()