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

# --- Cached client/spreadsheet handles ---
# google-auth Credentials objects refresh their own tokens as needed, so it's
# safe to keep reusing one authorized gspread client across calls instead of
# re-authenticating (and re-resolving the spreadsheet by title, which costs an
# extra Drive API lookup) on every single tool invocation. This matters
# because Sheets API quota is limited to 300 requests/60s per project and
# 60/60s per user.
_gspread_client: Optional[gspread.Client] = None
_spreadsheet_cache: Optional[gspread.Spreadsheet] = None


def _reset_gspread_cache() -> None:
    """Drops cached client/spreadsheet handles so the next call re-authenticates."""
    global _gspread_client, _spreadsheet_cache
    _gspread_client = None
    _spreadsheet_cache = None


# --- Helper Functions ---
def get_gspread_client() -> gspread.Client:
    """Authenticates and returns a cached gspread client, reused across calls."""
    global _gspread_client
    if _gspread_client is not None:
        return _gspread_client

    credentials_info = os.environ.get(CREDENTIALS_ENV_VAR)
    if not credentials_info:
        raise ValueError(f"Environment variable {CREDENTIALS_ENV_VAR} not set.")

    try:
        creds_data = json.loads(credentials_info)
        creds = Credentials.from_service_account_info(creds_data, scopes=REQUIRED_SCOPE)
        _gspread_client = gspread.authorize(creds)
        logger.info("Successfully authenticated with Google Sheets API.")
        return _gspread_client
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {CREDENTIALS_ENV_VAR}: {e}")
        raise ValueError(f"Invalid JSON in {CREDENTIALS_ENV_VAR}") from e
    except Exception as e:
        logger.error(f"Failed to authenticate Google Sheets client: {e}")
        raise


def get_spreadsheet() -> gspread.Spreadsheet:
    """Returns a cached handle to the main ContentSpark spreadsheet."""
    global _spreadsheet_cache
    if _spreadsheet_cache is not None:
        return _spreadsheet_cache
    client = get_gspread_client()
    _spreadsheet_cache = client.open(SPREADSHEET_NAME)
    return _spreadsheet_cache


def ensure_worksheet_exists(worksheet_name: str, headers: List[str]) -> bool:
    """Creates worksheet_name (with the given header row) if it doesn't
    already exist in the spreadsheet. manage_sheet_data's own actions all
    call spreadsheet.worksheet(worksheet_name) unconditionally before
    dispatching on `action`, so a missing worksheet fails before any action
    (including a hypothetical create action) could run -- this is a
    separate, standalone function for that reason. Self-healing schema,
    same rationale as scripts/run_stage.py's _ensure_column_header for new
    columns: no manual Google Sheets setup step required before a new stage
    that needs its own worksheet (e.g. repurposed_content) can run."""
    try:
        spreadsheet = get_spreadsheet()
        try:
            spreadsheet.worksheet(worksheet_name)
            return True  # Already exists.
        except gspread.exceptions.WorksheetNotFound:
            pass
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=100, cols=max(len(headers), 1))
        worksheet.append_row(headers, value_input_option="USER_ENTERED")
        logger.info(f"Created new worksheet '{worksheet_name}' with headers: {headers}")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure worksheet '{worksheet_name}' exists: {e}")
        return False


# In-process record of the keyword row get_keyword_tool claimed most recently
# (row_index + exact keyword text). Confirmed live: marking the row "used" the
# moment it's selected is NOT safe against the fallback runner's behavior --
# gemini-flash-latest ran the tool (row marked used), then the model call
# itself 503'd, and run_with_fallback re-ran the whole Triage Agent from
# scratch. The re-run invoked the tool again, found nothing "available"
# (the claim had already been burned), and returned an error -- the keyword
# the user had just prioritized was lost with no research done. With this
# state, a re-invocation in the same process re-issues the SAME row instead
# of scanning past it; the workflow that owns the process then either clears
# the state on success (row legitimately stays "used") or releases the row
# back to "available" on failure via release_keyword_claim().
_keyword_claim: Dict[str, Any] = {"row_index": None, "keyword": None}


def _keyword_preview(keyword: str, limit: int = 80) -> str:
    """Truncates a keyword for debug printing. Keywords can be entire YouTube
    transcripts (10k+ chars pasted into the sheet), and get_keyword_tool's
    per-row debug print used to dump each one in full -- a single run's log
    became an unreadable wall of text (confirmed live)."""
    return keyword if len(keyword) <= limit else keyword[: limit - 3] + "..."


@function_tool
def get_keyword_tool():
    """Fetches an available keyword from ContentSpark_Keywords and marks it as used."""
    try:
        # Idempotent re-claim: if this process already claimed a row, hand it
        # back instead of scanning again. This is what makes a fallback re-run
        # of the Triage Agent safe -- see _keyword_claim's comment.
        if _keyword_claim["keyword"] is not None:
            return {
                "selected_keyword": _keyword_claim["keyword"],
                "status_update": (
                    f"Row {_keyword_claim['row_index']} already claimed by this run; "
                    "re-issuing the same keyword (sheet unchanged)."
                ),
            }

        client = get_gspread_client()
        sheet = client.open("ContentSpark_Keywords").sheet1

        # Get all records
        records = sheet.get_all_records()
        # print(f"Records retrieved: {records}")  # Debug: Print all records

        if not records:
            return {"error": "No keywords available in sheet"}

        # Find the first available keyword
        for i, record in enumerate(records, 2):  # Start at row 2 (skip header)
            status = record.get("Status", "").strip().lower()
            print(f"Row {i}: Keyword={_keyword_preview(record.get('Keyword', ''))}, Status={status}")  # Debug
            if status == "available":
                keyword = record.get("Keyword", "")
                if not keyword:
                    print(f"Row {i}: Empty keyword, skipping")
                    continue
                # Mark as used
                sheet.update_cell(i, 2, "used")  # Status column is 2nd
                _keyword_claim["row_index"] = i
                _keyword_claim["keyword"] = keyword
                return {
                    "selected_keyword": keyword,
                    "status_update": f"Row {i} marked as used"
                }

        return {"error": "No available keywords found"}

    except Exception as e:
        # print(f"Error in get_keyword_tool: {str(e)}")  # Debug
        return {"error": f"Failed to fetch keyword: {str(e)}"}


def release_keyword_claim() -> bool:
    """Returns the most recently claimed keyword row to Status="available"
    and forgets the claim. Called by the research workflow whenever a run
    that claimed a keyword fails before its findings are persisted to
    research_data -- without this, any post-claim failure (model 503s, agent
    retry exhaustion, output agent failure) permanently burns the keyword
    even though nothing was researched. Best-effort: a row that no longer
    matches the claimed keyword (sheet edited/deleted mid-run) is left
    alone rather than blindly flipping some other row's Status cell."""
    row_index = _keyword_claim.get("row_index")
    keyword = str(_keyword_claim.get("keyword") or "")
    clear_keyword_claim()
    if row_index is None:
        return False
    try:
        client = get_gspread_client()
        sheet = client.open("ContentSpark_Keywords").sheet1
        current = str(sheet.cell(row_index, 1).value or "")
        # Prefix compare as well as exact: guards against a mid-run sheet edit
        # shifting rows, without needing the full (possibly huge) keyword to
        # round-trip identically through the API.
        if current != keyword and current[:100] != keyword[:100]:
            logger.warning(
                f"Not releasing keyword claim: row {row_index} now holds different "
                f"content ({_keyword_preview(current)}) than what was claimed."
            )
            return False
        sheet.update_cell(row_index, 2, "available")
        logger.info(f"Released keyword claim: row {row_index} back to 'available'.")
        return True
    except Exception as e:
        logger.warning(f"Failed to release keyword claim on row {row_index}: {e}")
        return False


def clear_keyword_claim() -> None:
    """Forgets the in-process claim WITHOUT touching the sheet -- used when a
    workflow that claimed a keyword completes successfully, so the row stays
    legitimately "used" but a later workflow in the same long-lived process
    (main.py's server) doesn't get handed the stale keyword again."""
    _keyword_claim["row_index"] = None
    _keyword_claim["keyword"] = None



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
    """
    attempt = 0
    while attempt < retries:
        try:
            spreadsheet = get_spreadsheet()
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
                logger.info(f"Retrieved {len(records)} records from {worksheet_name}")
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
                logger.info(f"Appending row to {worksheet_name}: {row_values}")
                worksheet.append_row(row_values, value_input_option=value_input_option)
                time.sleep(0.5)  # Small delay to ensure update is processed
                logger.info(f"Successfully appended row to {worksheet_name}")
                return {"status": "success", "message": f"Row appended to {worksheet_name}."}

            elif action == "insert_row":
                if not row_values or row_index is None:
                    return {"status": "error", "message": "row_values and row_index are required for insert_row action."}
                worksheet.insert_row(row_values, row_index, value_input_option=value_input_option)
                time.sleep(0.5)  # Small delay to ensure update is processed
                return {"status": "success", "message": f"Row inserted at index {row_index} in {worksheet_name}."}

            elif action == "update_row":
                if row_index is None or (not row_values and data is None):
                    return {"status": "error", "message": "row_index and row_values/data are required for update_row action."}
                values_to_update = row_values if row_values is not None else data # Prioritize row_values, fallback to data (if list-like)
                # Ensure data is a list for row update
                if not isinstance(values_to_update, list):
                    return {"status": "error", "message": "For update_row, row_values or data must be a list."}
                worksheet.update(f"A{row_index}", [values_to_update], value_input_option=value_input_option) # Update starting from column A
                
                # Verify that the update was successful by reading the row back
                time.sleep(0.5)  # Small delay to ensure update is processed
                updated_row = worksheet.row_values(row_index)
                
                # Compare values (making sure we're comparing the right number of values)
                if len(updated_row) >= len(values_to_update):
                    row_matches = all(updated_row[i] == str(values_to_update[i]) for i in range(len(values_to_update)))
                else:
                    # If updated row is shorter than expected, they don't match
                    row_matches = False
                
                if row_matches:
                    return {"status": "success", "message": f"Row {row_index} updated in {worksheet_name}."}
                else:
                    logger.error(f"Failed to update row {row_index} in {worksheet_name}. Expected: {values_to_update}, Got: {updated_row}")
                    return {"status": "error", "message": f"Failed to update row {row_index} in {worksheet_name}. Value verification failed."}

            elif action == "update_cell":
                if row_index is None or col_index is None or data is None: # data here is the cell value
                    return {"status": "error", "message": "row_index, col_index, and data (cell value) are required for update_cell action."}
                # For update_cell, data should be a simple string value
                cell_value = data if isinstance(data, str) else str(data)
                logger.info(f"Updating cell ({row_index}, {col_index}) in {worksheet_name} with value: {cell_value}")
                worksheet.update_cell(row_index, col_index, cell_value)
                
                # Verify that the update was successful by reading the cell back
                time.sleep(0.5)  # Small delay to ensure update is processed
                updated_value = worksheet.cell(row_index, col_index).value
                if updated_value == cell_value:
                    logger.info(f"Successfully updated cell ({row_index}, {col_index}) in {worksheet_name}")
                    return {"status": "success", "message": f"Cell ({row_index}, {col_index}) updated in {worksheet_name}."}
                else:
                    logger.error(f"Failed to update cell ({row_index}, {col_index}) in {worksheet_name}. Expected: {cell_value}, Got: {updated_value}")
                    return {"status": "error", "message": f"Failed to update cell ({row_index}, {col_index}) in {worksheet_name}. Value verification failed."}

            elif action == "update_cells":
                if cell_range is None or data is None: # data expected as List[List] for batch update
                    return {"status": "error", "message": "cell_range and data (List[List]) are required for update_cells action."}
                worksheet.update(cell_range, data, value_input_option=value_input_option)
                
                # Verify that the update was successful by reading the range back
                time.sleep(0.5)  # Small delay to ensure update is processed
                updated_range = worksheet.get(cell_range)
                
                # Compare the updated range with the expected data
                if updated_range == data:
                    return {"status": "success", "message": f"Range {cell_range} updated in {worksheet_name}."}
                else:
                    logger.error(f"Failed to update range {cell_range} in {worksheet_name}. Expected: {data}, Got: {updated_range}")
                    return {"status": "error", "message": f"Failed to update range {cell_range} in {worksheet_name}. Value verification failed."}

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
            # Drop cached client/spreadsheet in case the error was caused by a
            # stale/invalid cached handle, so the retry re-authenticates fresh.
            _reset_gspread_cache()
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
    data: Optional[Union[List[List[str]], str]] = None,
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
    """A generic tool to manage data in a Google Sheet worksheet.
    
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

def validate_row(row, row_index):
    """Validate the row content and FAQs."""
    errors = []
    content = row.get("Generated Content", "")
    faqs = row.get("FAQs", "")

    # H1 Check
    if not re.search(r'^# .+', content, re.MULTILINE):
        errors.append("Missing H1 heading")

    # H2 Sections Check
    h2_count = len(re.findall(r'^## .+', content, re.MULTILINE))
    if not 4 <= h2_count <= 6:
        errors.append(f"Found {h2_count} H2 sections, expected 4–6")

    # Internal Links Check
    internal_links = len(re.findall(r'\[.+?\]\(/blog/.+?\)', content))
    if not 2 <= internal_links <= 3:
        errors.append(f"Found {internal_links} internal links, expected 2–3")

    # External Links Check
    external_links = len(re.findall(r'\[.+?\]\(https?://.+?\)', content))
    if not 2 <= external_links <= 3:
        errors.append(f"Found {external_links} external links, expected 2–3")

    # FAQs Check
    try:
        faqs_list = json.loads(faqs)
        if not (isinstance(faqs_list, list) and 5 <= len(faqs_list) <= 7 and all("question" in faq and "answer" in faq for faq in faqs_list)):
            errors.append("Invalid FAQs: fewer than 5 pairs or missing question/answer")
    except json.JSONDecodeError:
        # Attempt Markdown parsing
        faq_matches = re.findall(r'\*+\s*\*\*Q:[^\n]+\*\*\n\s*\*\*A:\*\* [^\n]+', faqs, re.MULTILINE)
        if len(faq_matches) < 5:
            errors.append("Invalid FAQs: Markdown parse error or fewer than 5 pairs")
        else:
            logger.info(f"Parsed {len(faq_matches)} FAQs from Markdown at row {row_index}")

    return errors

def main():
    # Step 1: Get all records from generated_posts
    records = get_all_records(WORKSHEET_NAME)
    if not records:
        logger.error("No records found in generated_posts")
        return

    # Step 2: Find first approved and unposted row
    approved_row = None
    row_index = None
    for i, row in enumerate(records, start=2):  # 1-based index, header is row 1
        if row.get("Approve/Disapprove") == "Approve" and row.get("Published") == "No":
            approved_row = row
            row_index = i
            break

    if not approved_row:
        logger.error("No approved and unposted rows found")
        return

    logger.info(f"Found approved row at index {row_index}: {approved_row['Keyword/Topic']}")

    # Step 3: Validate row content
    errors = validate_row(approved_row, row_index)
    if errors:
        logger.error(f"Validation errors for row {row_index}: {errors}")
        return

    # Step 4: Get Published column index
    published_col_index = get_column_index(WORKSHEET_NAME, "Published")
    if not published_col_index:
        logger.error("Cannot proceed: Published column not found")
        return

    # Step 5: Simulate successful Sanity publishing (replace with actual post_to_sanity_tool call)
    sanity_success = True  # Assume success for testing; replace with actual tool call
    if sanity_success:
        # Step 6: Update Published column
        success = update_cell(WORKSHEET_NAME, row_index, published_col_index, "Yes")
        if success:
            logger.info(f"Successfully updated Published column for row {row_index}")
        else:
            logger.error(f"Failed to update Published column for row {row_index}")