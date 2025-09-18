import os
import json
import gspread
from google.oauth2.service_account import Credentials
from typing import List, TypedDict, Optional, Union, Any, Dict, Literal
import logging
import time
from agents import function_tool
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file if present

# Configure logging for better error tracking
logging.basicConfig(level=logging.INFO) # Adjust level as needed (DEBUG, INFO, WARNING, ERROR)
logger = logging.getLogger(__name__)

# --- Constants ---
REQUIRED_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

CREDENTIALS_ENV_VAR = "GOOGLE_CREDENTIALS" # Ensure this matches your environment variable name
SPREADSHEET_NAME = "ContentSpark" # Your main spreadsheet name

# --- Helper Functions ---
def get_gspread_client() -> gspread.Client:
    """Authenticates and returns a gspread client."""
    credentials_info = os.environ.get(CREDENTIALS_ENV_VAR)
    if not credentials_info:
        raise ValueError(f"Environment variable {CREDENTIALS_ENV_VAR} not set.")

    try:
        creds_data = json.loads(credentials_info)
        creds = Credentials.from_service_account_info(creds_data, scopes=REQUIRED_SCOPE)
        client = gspread.authorize(creds)
        logger.info("Successfully authenticated with Google Sheets API.")
        return client
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {CREDENTIALS_ENV_VAR}: {e}")
        raise ValueError(f"Invalid JSON in {CREDENTIALS_ENV_VAR}") from e
    except Exception as e:
        logger.error(f"Failed to authenticate Google Sheets client: {e}")
        raise


@function_tool
def get_keyword_tool():
    """Fetches an available keyword from ContentSpark_Keywords and marks it as used."""
    try:
        # Authenticate with Google Sheets
        scope = REQUIRED_SCOPE
        creds_info = os.environ.get(CREDENTIALS_ENV_VAR)
        creds = Credentials.from_service_account_info(json.loads(creds_info), scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open("ContentSpark_Keywords").sheet1

        # Get all records
        records = sheet.get_all_records()
        # print(f"Records retrieved: {records}")  # Debug: Print all records

        if not records:
            return {"error": "No keywords available in sheet"}

        # Find the first available keyword
        for i, record in enumerate(records, 2):  # Start at row 2 (skip header)
            status = record.get("Status", "").strip().lower()
            print(f"Row {i}: Keyword={record.get('Keyword', '')}, Status={status}")  # Debug
            if status == "available":
                keyword = record.get("Keyword", "")
                if not keyword:
                    print(f"Row {i}: Empty keyword, skipping")
                    continue
                # Mark as used
                sheet.update_cell(i, 2, "used")  # Status column is 2nd
                return {
                    "selected_keyword": keyword,
                    "status_update": f"Row {i} marked as used"
                }
        
        return {"error": "No available keywords found"}
    
    except Exception as e:
        # print(f"Error in get_keyword_tool: {str(e)}")  # Debug
        return {"error": f"Failed to fetch keyword: {str(e)}"}



# --- Refined Type Definitions for Schema Generation ---

# Define specific types for data based on action
# For actions returning lists of rows/columns/cells
SheetDataList = List[List[str]] # Assuming data is primarily strings/numbers rendered as strings

# For actions returning a single row/column
SheetDataRow = List[str]

# For actions returning key-value pairs (e.g., get_range for a small range)
SheetDataDict = Dict[str, Any] # Less ideal, but sometimes necessary. Try to be more specific if possible.

# For actions returning all records
SheetRecords = List[Dict[str, Any]] # List of dictionaries, keys are headers

# Specific result structures for different actions (simplified for schema)
class BaseResult(Dict[str, Any]): # Use a base Dict for flexibility in schema gen
    status: str # 'success' or 'error'
    message: str # Descriptive message

class GetWorksheetResult(BaseResult):
    worksheet_title: str
    row_count: int
    col_count: int

class GetAllRecordsResult(BaseResult):
    data: SheetRecords
    count: int

class GetRowResult(BaseResult):
     data: SheetDataRow
     row_index: int

class GetColumnResult(BaseResult):
     data: SheetDataRow # Columns are also lists of values
     col_index: int

class GetRangeResult(BaseResult):
     data: SheetDataList # or SheetDataDict if range is small/structured
     range: str

class FindRowResult(BaseResult):
     found: bool
     row_index: Optional[int] # None if not found
     data: Optional[Dict[str, str]] # Record dict if found, None if not


# --- Generic Sheet Management Tool ---
# Simplified function signature for schema generation
# Use Union of specific action literals for better hinting
def manage_sheet_data(
    worksheet_name: str,
    action: Literal[ # Specify allowed actions explicitly
        "get_worksheet", "get_all_records", "get_row", "get_column",
        "get_range", "append_row", "insert_row", "update_row",
        "update_cell", "update_cells", "delete_row", "clear_sheet",
        "find_row_by_key"
    ],
    # Use Optional types for parameters that are not always required
    data: Optional[List[List[str]]] = None, # Simplified for schema, e.g., for update_cells
    row_values: Optional[List[str]] = None, # Simplified for schema, e.g., for append/insert
    row_index: Optional[int] = None,
    col_values: Optional[List[str]] = None, # Simplified for schema
    col_index: Optional[int] = None,
    cell_range: Optional[str] = None,
    value_input_option: str = "USER_ENTERED", # Keep as string, values are constrained by gspread
    key_column: Optional[str] = None,
    key_value: Optional[str] = None, # Simplify key value type
    return_all_records: bool = False, # Remove, handled by action=get_all_records
    num_rows: Optional[int] = None, # Remove if not used
    num_cols: Optional[int] = None, # Remove if not used
    retries: int = 3,
    delay: float = 1.0
) -> BaseResult: # Simplified return type hint for schema compatibility
    """
    A generic tool to manage data in a Google Sheet worksheet.

    Args:
        worksheet_name (str): Name of the worksheet (sub-sheet) within the main spreadsheet.
        action (str): The action to perform. Options:
                      'get_worksheet', 'get_all_records', 'get_row', 'get_column',
                      'get_range', 'append_row', 'insert_row', 'update_row',
                      'update_cell', 'update_cells', 'delete_row', 'clear_sheet',
                      'find_row_by_key'.
        data (List[List[str]], optional): Data for batch write/update actions (e.g., update_cells).
        row_values (List[str], optional): Values for a single row (append/insert).
        row_index (int, optional): 1-based row index for read/update/delete.
        col_values (List[str], optional): Values for a single column (update).
        col_index (int, optional): 1-based column index for read/update.
        cell_range (str, optional): Cell range (e.g., 'A1:C10') for get_range/update_cells.
        value_input_option (str): How to interpret values ('RAW', 'USER_ENTERED').
        key_column (str, optional): Header name of the column to use as a lookup key (for find_row_by_key).
        key_value (str, optional): Value in the key column to find the matching row (for find_row_by_key).
        retries (int): Number of times to retry the operation on failure.
        delay (float): Delay in seconds between retries.

    Returns:
        BaseResult: A dictionary containing the result. The exact structure depends on the action.
                    Success typically includes 'status': 'success' and relevant data.
                    Errors include 'status': 'error' and 'message': <error_details>.
                    Common fields: status (str), message (str).
                    Action-specific fields might include:
                    - data (varies), row_index (int), col_index (int), range (str), found (bool), count (int),
                      worksheet_title (str), row_count (int), col_count (int).
    """
    client = None
    attempt = 0
    while attempt < retries:
        try:
            client = get_gspread_client()
            spreadsheet = client.open(SPREADSHEET_NAME)
            # --- Key Change: Open the specific worksheet by name ---
            worksheet = spreadsheet.worksheet(worksheet_name)
            # -------------------------------------------------------

            logger.debug(f"Performing action '{action}' on worksheet '{worksheet_name}', attempt {attempt + 1}")

            if action == "get_worksheet":
                # Returns basic worksheet info
                 return {
                    "status": "success",
                    "worksheet_title": worksheet.title,
                    "row_count": worksheet.row_count,
                    "col_count": worksheet.col_count
                }

            elif action == "get_all_records":
                records = worksheet.get_all_records()
                return {"status": "success", "data": records, "count": len(records)}

            elif action == "get_row":
                if row_index is None:
                     return {"status": "error", "message": "row_index is required for get_row action."}
                row_data = worksheet.row_values(row_index)
                return {"status": "success", "data": row_data, "row_index": row_index}

            elif action == "get_column":
                if col_index is None:
                    return {"status": "error", "message": "col_index is required for get_column action."}
                col_data = worksheet.col_values(col_index)
                return {"status": "success", "data": col_data, "col_index": col_index}

            elif action == "get_range":
                if cell_range is None:
                    return {"status": "error", "message": "cell_range is required for get_range action."}
                range_data = worksheet.get(cell_range)
                return {"status": "success", "data": range_data, "range": cell_range}

            elif action == "append_row":
                if not row_values:
                     return {"status": "error", "message": "row_values is required for append_row action."}
                worksheet.append_row(row_values, value_input_option=value_input_option)
                return {"status": "success", "message": f"Row appended to {worksheet_name}."}

            elif action == "insert_row":
                if not row_values or row_index is None:
                    return {"status": "error", "message": "row_values and row_index are required for insert_row action."}
                worksheet.insert_row(row_values, row_index, value_input_option=value_input_option)
                return {"status": "success", "message": f"Row inserted at index {row_index} in {worksheet_name}."}

            elif action == "update_row":
                if row_index is None or (not row_values and data is None):
                    return {"status": "error", "message": "row_index and row_values/data are required for update_row action."}
                values_to_update = row_values if row_values is not None else data # Prioritize row_values, fallback to data (if list-like)
                # Ensure data is a list for row update
                if not isinstance(values_to_update, list):
                    return {"status": "error", "message": "For update_row, row_values or data must be a list."}
                worksheet.update(f"A{row_index}", [values_to_update], value_input_option=value_input_option) # Update starting from column A
                return {"status": "success", "message": f"Row {row_index} updated in {worksheet_name}."}

            elif action == "update_cell":
                if row_index is None or col_index is None or data is None: # data here is the cell value
                    return {"status": "error", "message": "row_index, col_index, and data (cell value) are required for update_cell action."}
                worksheet.update_cell(row_index, col_index, data, value_input_option=value_input_option)
                return {"status": "success", "message": f"Cell ({row_index}, {col_index}) updated in {worksheet_name}."}

            elif action == "update_cells":
                if cell_range is None or data is None: # data expected as List[List] for batch update
                    return {"status": "error", "message": "cell_range and data (List[List]) are required for update_cells action."}
                worksheet.update(cell_range, data, value_input_option=value_input_option)
                return {"status": "success", "message": f"Range {cell_range} updated in {worksheet_name}."}

            elif action == "delete_row":
                if row_index is None:
                    return {"status": "error", "message": "row_index is required for delete_row action."}
                worksheet.delete_rows(row_index) # gspread uses 1-based indexing
                return {"status": "success", "message": f"Row {row_index} deleted from {worksheet_name}."}

            elif action == "clear_sheet":
                worksheet.clear()
                return {"status": "success", "message": f"All data cleared from {worksheet_name}."}

            elif action == "find_row_by_key":
                if key_column is None or key_value is None:
                    return {"status": "error", "message": "key_column and key_value are required for find_row_by_key action."}
                try:
                    records = worksheet.get_all_records()
                except Exception as e:
                    return {"status": "error", "message": f"Failed to fetch records for lookup: {str(e)}"}
                headers = worksheet.row_values(1) # Assumes headers are in row 1
                if key_column not in headers:
                    return {"status": "error", "message": f"Column '{key_column}' not found in worksheet headers."}
                key_col_index = headers.index(key_column) + 1 # Convert to 1-based index

                for i, record in enumerate(records):
                     # Check value in the specific key column
                    if str(record.get(key_column, "")) == str(key_value):
                         # Return 1-based row index (header is row 1, so data starts at row 2)
                        found_row_index = i + 2
                        return {
                            "status": "success",
                            "found": True,
                            "row_index": found_row_index,
                            "data": record,
                            "message": f"Row found for {key_column} = {key_value} at index {found_row_index}."
                        }
                return {
                    "status": "success",
                    "found": False,
                    "row_index": None,
                    "data": None,
                    "message": f"No row found for {key_column} = {key_value}."
                }

            else:
                return {"status": "error", "message": f"Unsupported action: {action}"}

        except gspread.exceptions.WorksheetNotFound:
            error_msg = f"Worksheet '{worksheet_name}' not found in spreadsheet '{SPREADSHEET_NAME}'."
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
        except gspread.exceptions.APIError as e:
            # Handle specific API errors, potentially retryable ones
            error_details = e.response.json() if hasattr(e, 'response') and e.response else str(e)
            logger.warning(f"API Error on attempt {attempt + 1} for {action}: {error_details}")
            if attempt < retries - 1:
                 time.sleep(delay) # Use time.sleep
                 attempt += 1
                 continue # Retry
            else:
                 return {"status": "error", "message": f"API Error after {retries} attempts: {error_details}"}
        except Exception as e:
            logger.error(f"Unexpected error during sheet operation '{action}' on '{worksheet_name}': {e}", exc_info=True)
            return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}

    # This point should ideally not be reached if retries are handled correctly in the loop
    return {"status": "error", "message": "Operation failed after maximum retries due to unknown reasons."}


@function_tool # Apply the decorator *after* defining the function with refined hints
def manage_sheet_data_tool( # Wrapper function for the decorator if needed, or apply directly
    worksheet_name: str,
    action: Literal[
        "get_worksheet", "get_all_records", "get_row", "get_column",
        "get_range", "append_row", "insert_row", "update_row",
        "update_cell", "update_cells", "delete_row", "clear_sheet",
        "find_row_by_key"
    ],
    data: Optional[List[List[str]]] = None,
    row_values: Optional[List[str]] = None,
    row_index: Optional[int] = None,
    col_values: Optional[List[str]] = None,
    col_index: Optional[int] = None,
    cell_range: Optional[str] = None,
    value_input_option: str = "USER_ENTERED",
    key_column: Optional[str] = None,
    key_value: Optional[str] = None,
    retries: int = 3,
    delay: float = 1.0
) -> BaseResult:
    """A generic tool to manage data in a Google Sheet worksheet."""
    # Delegate to the main implementation
    return manage_sheet_data(
        worksheet_name=worksheet_name,
        action=action,
        data=data,
        row_values=row_values,
        row_index=row_index,
        col_values=col_values,
        col_index=col_index,
        cell_range=cell_range,
        value_input_option=value_input_option,
        key_column=key_column,
        key_value=key_value,
        retries=retries,
        delay=delay
    )




def run_test(description, result):
    """Helper function to print test results."""
    print(f"\n--- Test: {description} ---")
    # Pretty print the result dictionary
    print(json.dumps(result, indent=2, default=str)) # default=str handles non-serializable objects
    # Basic check for success/failure based on 'status' key
    if result.get("status") == "success":
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL (Check error message)")

def main():
    """Main function to run test cases."""
    print("Starting tests for manage_sheet_data function...")

    # --- Configuration ---
    # Replace these with actual names/values relevant to your spreadsheet setup
    TEST_WORKSHEET_NAME = "test_sheet" # Use a dedicated test worksheet
    TEST_KEYWORD_COLUMN = "Name"       # Example column name for find_row_by_key test
    TEST_KEYWORD_VALUE = "Test User"   # Example value to search for

    # --- Test Cases ---

    # 1. Get Worksheet Info
    result1 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="get_worksheet"
    )
    run_test("Get Worksheet Info", result1)

    # 2. Get All Records (assuming the sheet has headers and data)
    result2 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="get_all_records"
    )
    run_test("Get All Records", result2)

    # 3. Append a new row
    new_row_data = ["Test User", "test@example.com", "Testing", "2023-10-27T10:00:00Z"] # Example data
    result3 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="append_row",
        row_values=new_row_data
    )
    run_test("Append Row", result3)

    # 4. Find the row we just appended (using the key column and value defined above)
    # This test depends on the previous append_row test being successful
    # and the worksheet having a column named TEST_KEYWORD_COLUMN with the value TEST_KEYWORD_VALUE.
    result4 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="find_row_by_key",
        key_column=TEST_KEYWORD_COLUMN,
        key_value=TEST_KEYWORD_VALUE
    )
    run_test(f"Find Row by Key ({TEST_KEYWORD_COLUMN} = {TEST_KEYWORD_VALUE})", result4)

    # 5. Get a specific row (e.g., the header row, row 1)
    result5 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="get_row",
        row_index=1 # Get the first row (headers)
    )
    run_test("Get Row (Header Row)", result5)

    # 6. Get a specific column (e.g., column 1)
    result6 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="get_column",
        col_index=1 # Get the first column
    )
    run_test("Get Column (First Column)", result6)

    # 7. Get a specific range (e.g., A1:B2)
    result7 = manage_sheet_data(
        worksheet_name=TEST_WORKSHEET_NAME,
        action="get_range",
        cell_range="A1:B2" # Adjust range as needed
    )
    run_test("Get Range (A1:B2)", result7)

    # 8. Test Error Handling (e.g., Invalid worksheet name)
    result8 = manage_sheet_data(
        worksheet_name="THIS_SHEET_DOES_NOT_EXIST_12345", # Intentionally incorrect name
        action="get_worksheet"
    )
    run_test("Error Handling (Invalid Worksheet Name)", result8)

    # --- Conditional Tests (based on previous results) ---
    # Example: Update the row found in test 4, then delete it.
    # These require the previous tests to have succeeded in specific ways.

    if result4.get("status") == "success" and result4.get("found") and result4.get("row_index"):
        found_row_index = result4["row_index"]
        print(f"\n--- Conditional Tests (Row found at index {found_row_index}) ---")

        # 9. Update the found row
        updated_row_data = ["Updated Test User", "updated_test@example.com", "Updated", "2023-10-27T11:00:00Z"]
        result9 = manage_sheet_data(
            worksheet_name=TEST_WORKSHEET_NAME,
            action="update_row",
            row_index=found_row_index,
            row_values=updated_row_data
        )
        run_test(f"Update Found Row (Index {found_row_index})", result9)

        # 10. Delete the updated row
        result10 = manage_sheet_data(
            worksheet_name=TEST_WORKSHEET_NAME,
            action="delete_row",
            row_index=found_row_index
        )
        run_test(f"Delete Updated Row (Index {found_row_index})", result10)

    print("\n--- All tests completed. ---")


if __name__ == "__main__":
    main()