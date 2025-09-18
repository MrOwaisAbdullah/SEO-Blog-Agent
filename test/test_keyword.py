from tools import get_keyword_tool

def test_get_keyword():
    """Tests the get_keyword_tool function."""
    try:
        # Call the tool
        result = get_keyword_tool()
        print("Test Result:", result)

        # Validate the result
        if "error" in result:
            print(f"Test Failed: {result['error']}")
            return
        
        if "selected_keyword" in result and "status_update" in result:
            print("Test Passed: Keyword retrieved successfully")
            print(f"Selected Keyword: {result['selected_keyword']}")
            print(f"Status Update: {result['status_update']}")
        else:
            print("Test Failed: Unexpected response format")
    
    except Exception as e:
        print(f"Test Failed: Unexpected error - {str(e)}")

if __name__ == "__main__":
    test_get_keyword()