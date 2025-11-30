Qase Test Run Synchronizer
Automate your QA workflow. This Python script synchronizes test results from one Qase project (Project B) to another (Project A) with precision and reliability. No more manual updates, just seamless integration.

What This Script Does

Fetches test results from a source run in Project B.
Maps test cases between Project B and Project A using:

Custom Field Matching (preferred)
Fallback by Case ID
Fallback by Normalized Title


Posts results into the target run in Project A, preserving:

Status (passed, failed, skipped, blocked)
Execution time
Comments, stack traces, and attachments




How It Works


Environment Setup
The script loads configuration from a .env file:

QASE_API_TOKEN → Your Qase API token.
PROJECT_A_CODE / PROJECT_B_CODE → Project codes.
RUN_A_ID / RUN_B_ID → IDs of the runs to sync.
CUSTOM_FIELD_B_IN_A → Custom field key for mapping.



Mapping Logic

Primary: Custom field linked_case_id_in_A in both projects.
Fallback: Case ID or normalized title matching.



Result Sync

Fetch results from Run B.
Post them to Run A via Qase API with retries for stability.




Requirements
Install the following Python libraries before running the script:

requests → Handles HTTP calls to Qase API with retry logic.
python-dotenv → Loads environment variables from .env.


⚙️ Installation

1. Run these commands in Git Bash or Windows PowerShell:

pip install requests python-dotenv

Usage

Create a .env file in the same directory as the script:

QASE_API_TOKEN=your_api_token
QASE_HOST=qase.io
PROJECT_A_CODE=PA
PROJECT_B_CODE=PB
RUN_A_ID=11
RUN_B_ID=2
CUSTOM_FIELD_B_IN_A=linked_case_id_in_A

2. Execute the script:

python sync_qase_runs.py

File Structure
sync_qase_runs.py   # Main script
.env                # Environment configuration

Key Features

Retry mechanism for API stability.
Defensive pagination for large datasets.
Smart mapping with multiple fallback strategies.
Detailed logging for synced and skipped cases.


⚠️ Important Notes

Requires Qase API token with appropriate permissions.
Ensure case titles or custom fields are consistent across projects.
Supports attachments and comments during sync.


💡 Why Use This Script?

Save hours of manual syncing.
Reduce errors in cross-project reporting.
Automate repetitive QA tasks with confidence.