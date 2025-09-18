# test_adapter.py
import os
from lib.sanity_adapter import SanityAdapter # Adjust import
from dotenv import load_dotenv
load_dotenv()

if __name__ == "__main__":
    adapter = SanityAdapter(
        project_id=os.environ['SANITY_PROJECT_ID'],
        dataset=os.environ.get('SANITY_DATASET', 'production'),
        token=os.environ['SANITY_API_TOKEN']
    )
    test_doc = {
        "_type": "post",
        "title": "Test Post from Direct Adapter Call",
        "slug": {"_type": "slug", "current": "test-direct-adapter-call"}
        # Add other required fields if necessary for your schema
    }
    result = adapter.create_document(test_doc)
    print("Direct Adapter Test Result:")
    print(result)
    # Check Sanity Studio for this post.