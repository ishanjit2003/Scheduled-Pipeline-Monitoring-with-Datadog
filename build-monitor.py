import os
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta

# ======================================
# Configuration
# ======================================

ORG = "datadogCS"
PROJECT = "datadog"

PAT = os.environ["AZDO_PAT"]
DD_API_KEY = os.environ["DD_API_KEY"]

LOOKBACK_HOURS = 12
LOG_LINES = 100

# # Monitoring pipelines to ignore
# EXCLUDED_PIPELINES = [
#     "Monitoring"
# ]

CURRENT_PIPELINE = os.environ.get(
    "BUILD_DEFINITIONNAME",
    ""
)

# ======================================
# Helper Functions
# ======================================

def azdo_get(url):
    response = requests.get(
        url,
        auth=HTTPBasicAuth("", PAT)
    )

    response.raise_for_status()

    if not response.text.strip():
        print(f"Empty response from Azure DevOps: {url}")
        return {}

    try:
        return response.json()
    except ValueError:
        print(f"Invalid JSON response from Azure DevOps: {url}")
        print(response.text[:500])
        return {}


def get_log_content(build_id, log_id):
    url = (
        f"https://dev.azure.com/{ORG}/{PROJECT}"
        f"/_apis/build/builds/{build_id}/logs/{log_id}"
        f"?api-version=7.1"
    )

    response = requests.get(
        url,
        auth=HTTPBasicAuth("", PAT)
    )

    if response.status_code != 200:
        return "Unable to download log"

    lines = response.text.splitlines()

    return "\n".join(lines[-LOG_LINES:])


# ======================================
# Get Scheduled Builds
# ======================================

min_time = (
    datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)
).strftime("%Y-%m-%dT%H:%M:%SZ")

builds_url = (
    f"https://dev.azure.com/{ORG}/{PROJECT}"
    f"/_apis/build/builds"
    f"?minTime={min_time}"
    f"&api-version=7.1"
)

print(f"Fetching builds since: {min_time}")

builds = azdo_get(builds_url).get("value", [])

print(f"Total builds fetched: {len(builds)}")

# ======================================
# Process Failed Scheduled Builds
# ======================================

failed_count = 0

for build in builds:

    reason = str(build.get("reason", "")).lower()
    result = str(build.get("result", "")).lower()

    if reason != "schedule":
        continue

    if result != "failed":
        continue

    build_id = build["id"]
    pipeline_name = build["definition"]["name"]

    # Skip monitoring pipelines
    # if pipeline_name in EXCLUDED_PIPELINES:
    #     print(f"Skipping monitoring pipeline: {pipeline_name}")
    #     continue

    if pipeline_name == CURRENT_PIPELINE:
        print(
            f"Skipping current monitoring pipeline: "
            f"{pipeline_name}"
        )
        continue

    failed_count += 1

    build_url = (
        f"https://dev.azure.com/{ORG}/{PROJECT}"
        f"/_build/results?buildId={build_id}"
    )

    print(f"\nFailed Scheduled Build Found: {pipeline_name}")

    # ======================================
    # Get Timeline
    # ======================================

    timeline_url = (
        f"https://dev.azure.com/{ORG}/{PROJECT}"
        f"/_apis/build/builds/{build_id}/timeline"
        f"?api-version=7.1"
    )

    timeline = azdo_get(timeline_url)

    if not timeline:
        print(
            f"Timeline unavailable for Build ID: {build_id}"
        )
        timeline = {"records": []}

    failed_stage = "Unknown"
    failed_job = "Unknown"
    error_message = "No error message available"
    error_log = "No log available"

    for record in timeline.get("records", []):

        if str(record.get("result", "")).lower() != "failed":
            continue

        failed_job = record.get("name", "Unknown")

        if record.get("type") == "Stage":
            failed_stage = record.get("name", "Unknown")

        issues = record.get("issues", [])

        if issues:
            error_message = issues[0].get(
                "message",
                "No error message available"
            )

        log_info = record.get("log")

        if log_info and log_info.get("id"):
            error_log = get_log_content(
                build_id,
                log_info["id"]
            )

        break

    print("=" * 80)
    print(f"Pipeline     : {pipeline_name}")
    print(f"Build ID     : {build_id}")
    print(f"Failed Stage : {failed_stage}")
    print(f"Failed Job   : {failed_job}")
    print(f"Error        : {error_message}")
    print(f"Link         : {build_url}")
    print("=" * 80)

    # ======================================
    # Send To Datadog Logs
    # ======================================

    payload = [
        {
            "ddsource": "azuredevops",
            "service": "pipeline-monitoring",

            "ddtags": (
                f"pipeline:{pipeline_name},"
                f"buildid:{build_id},"
                "type:build,"
                "status:failed,"
                "trigger:schedule,"
                "source:azuredevops"
            ),

            "pipeline_name": pipeline_name,
            "build_id": str(build_id),
            "failed_stage": failed_stage,
            "failed_job": failed_job,
            "error_message": error_message,
            "pipeline_url": build_url,

            "message": (
                f"Pipeline Failure\n\n"
                f"Pipeline: {pipeline_name}\n"
                f"Build ID: {build_id}\n"
                f"Failed Stage: {failed_stage}\n"
                f"Failed Job: {failed_job}\n\n"
                f"Error:\n"
                f"{error_message}\n\n"
                f"Failed Log Snippet:\n"
                f"{error_log}\n\n"
                f"Pipeline URL:\n"
                f"{build_url}"
            )
        }
    ]

    dd_response = requests.post(
        "https://http-intake.logs.datadoghq.com/api/v2/logs",
        headers={
            "DD-API-KEY": DD_API_KEY,
            "Content-Type": "application/json"
        },
        json=payload
    )

    if dd_response.status_code in [200, 202]:
        print(
            f"Successfully sent Datadog log for "
            f"{pipeline_name}"
        )
    else:
        print(
            f"Failed sending log to Datadog: "
            f"{dd_response.status_code}"
        )
        print(dd_response.text)

print(f"\nTotal failed scheduled builds found: {failed_count}")
print("Monitoring completed.")