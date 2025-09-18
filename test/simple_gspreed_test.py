# simple_gspread_test.py
import gspread
import json
import os
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
# Load environment variables from .env file if present
load_dotenv()

REQUIRED_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

def test_access():
    try:
        credentials_info = os.environ.get("GOOGLE_CREDENTIALS")
        if not credentials_info:
            print("Error: GOOGLE_CREDENTIALS environment variable not set.")
            return

        creds_data = json.loads(credentials_info)
        creds = Credentials.from_service_account_info(creds_data, scopes=REQUIRED_SCOPE)
        client = gspread.authorize(creds)
        print("Authentication successful.")

        # List first few spreadsheets to see if ContentSpark is listed
        print("\n--- Listing first 5 accessible spreadsheets ---")
        spreadsheets = client.openall() # This might fail if too many
        for i, sheet in enumerate(spreadsheets[:5]):
             print(f"  {i+1}. {sheet.title}")

        # Try to open ContentSpark specifically
        SPREADSHEET_NAME = "ContentSpark" # Make sure this matches exactly
        print(f"\n--- Trying to open '{SPREADSHEET_NAME}' ---")
        spreadsheet = client.open(SPREADSHEET_NAME)
        print(f"Success! Opened '{spreadsheet.title}'")

        # List worksheets within ContentSpark
        print(f"\n--- Worksheets in '{spreadsheet.title}' ---")
        worksheets = spreadsheet.worksheets()
        for ws in worksheets:
            print(f"  - {ws.title}")

    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Error: Spreadsheet '{SPREADSHEET_NAME}' not found. Check name and permissions.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    test_access()