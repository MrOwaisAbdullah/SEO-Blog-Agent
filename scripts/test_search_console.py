"""
Standalone, one-off diagnostic: verifies the GOOGLE_CREDENTIALS service
account (the same one this pipeline already uses for Google Sheets) actually
has working Search Console access for owaisabdullah.dev, before building the
real integration.

Read-only and side-effect-free -- doesn't touch any pipeline sheets/state,
doesn't write anything to Search Console. Uses a raw REST call via `requests`
+ `google.oauth2.service_account.Credentials` rather than pulling in the
`google-api-python-client` package, since that's a new dependency this
project doesn't otherwise need just for one diagnostic script.

Run with GOOGLE_CREDENTIALS set in the environment:
    uv run python scripts/test_search_console.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
SITE_URL = "https://owaisabdullah.dev/"


def _get_access_token() -> str:
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def main() -> None:
    try:
        token = _get_access_token()
    except KeyError:
        print("ERROR: GOOGLE_CREDENTIALS is not set in the environment.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to authenticate the service account: {e}", file=sys.stderr)
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    print("Step 1: Listing sites this service account can see...")
    resp = requests.get("https://www.googleapis.com/webmasters/v3/sites", headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"FAILED ({resp.status_code}): {resp.text}")
        print(
            "\nThis usually means either the Search Console API isn't enabled in the "
            "same Google Cloud project as this service account, or the service account "
            "hasn't actually been added as a Search Console user yet."
        )
        sys.exit(1)

    sites = resp.json().get("siteEntry", [])
    if not sites:
        print("Authenticated OK, but this service account has access to ZERO sites.")
        print("Check that it was added under the exact right Search Console property.")
        sys.exit(1)

    print(f"Service account can see {len(sites)} site(s):")
    for site in sites:
        print(f"   - {site['siteUrl']} (permission: {site['permissionLevel']})")

    target_visible = any(s["siteUrl"].rstrip("/") == SITE_URL.rstrip("/") for s in sites)
    if not target_visible:
        print(f"\nWARNING: {SITE_URL} is not in the list above -- check the property URL matches exactly.")
        print(
            "Search Console sometimes has both a Domain property and a URL-prefix property "
            "for the same site; make sure the service account was added to the one that's "
            "actually verified/active."
        )
        sys.exit(1)

    print(f"\nStep 2: Pulling a real Search Analytics query for {SITE_URL} (last 7 days)...")
    end_date = datetime.now(timezone.utc).date() - timedelta(days=3)  # GSC data has a ~2-3 day lag
    start_date = end_date - timedelta(days=7)
    query_body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "rowLimit": 10,
    }
    resp = requests.post(
        f"https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(SITE_URL, safe='')}/searchAnalytics/query",
        headers=headers,
        json=query_body,
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"FAILED ({resp.status_code}): {resp.text}")
        sys.exit(1)

    rows = resp.json().get("rows", [])
    if not rows:
        print(
            "Query succeeded but returned 0 rows -- this can be normal for a low-traffic "
            "site or a short/no-data date range, not necessarily a permissions problem."
        )
    else:
        print(f"SUCCESS -- {len(rows)} query row(s) returned, e.g.:")
        for row in rows[:5]:
            query = row["keys"][0]
            print(f"   '{query}': {row['clicks']} clicks, {row['impressions']} impressions, position {row['position']:.1f}")

    print("\n✅ Search Console access is fully working end-to-end.")


if __name__ == "__main__":
    main()
