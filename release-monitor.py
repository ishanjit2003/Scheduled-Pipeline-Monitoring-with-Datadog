import os
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta, timezone

# ======================================
# Configuration
# ======================================

ORG = "datadogCS"
PROJECT = "datadog"

PAT = os.environ["AZDO_PAT"]
DD_API_KEY = os.environ["DD_API_KEY"]

LOOKBACK_HOURS = 12

# ======================================
# Helper Function
# ======================================

def azdo_get(url):
    response = requests.get(
        url,
        auth=HTTPBasicAuth("", PAT)
    )
    response.raise_for_status()
    return response.json()


# ======================================
# Calculate Lookback Time
# ======================================

min_time = datetime.now(timezone.utc) - timedelta(
    hours=LOOKBACK_HOURS
)

print(
    f"Fetching scheduled releases since: "
    f"{min_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
)


# ======================================
# Get Releases
# ======================================

release_url = (
    f"https://vsrm.dev.azure.com/{ORG}/{PROJECT}"
    f"/_apis/release/releases"
    f"?api-version=7.1"
)

releases = azdo_get(release_url).get("value", [])

print(f"Total releases fetched: {len(releases)}")

failed_release_count = 0


# ======================================
# Process Scheduled Failed Releases
# ======================================

for release in releases:

    reason = str(
        release.get("reason", "")
    ).lower()

    if reason != "schedule":
        continue

    # ==================================
    # Check Release Creation Time
    # ==================================

    created_on = release.get("createdOn")

    if not created_on:
        print(
            f"Skipping release {release.get('id')}: "
            f"creation time unavailable"
        )
        continue

    try:
        release_time = datetime.fromisoformat(
            created_on.replace("Z", "+00:00")
        )
    except ValueError:
        print(
            f"Skipping release {release.get('id')}: "
            f"invalid creation time"
        )
        continue

    if release_time < min_time:
        continue

    release_id = release["id"]

    release_name = (
        release.get("releaseDefinition", {})
        .get("name", "Unknown")
    )

    # ==================================
    # Get Release Details
    # ==================================

    details_url = (
        f"https://vsrm.dev.azure.com/{ORG}/{PROJECT}"
        f"/_apis/release/releases/{release_id}"
        f"?api-version=7.1"
    )

    details = azdo_get(details_url)

    # ==================================
    # Process Environments
    # ==================================

    for env in details.get("environments", []):

        env_status = str(
            env.get("status", "")
        ).lower()

        if env_status not in ["failed", "rejected"]:
            continue

        failed_release_count += 1

        environment_name = env.get(
            "name",
            "Unknown"
        )

        failed_task = "Unknown"
        error_message = "No error message available"

        # ==================================
        # Traverse Deployment Hierarchy
        # ==================================

        for deploy_step in env.get(
            "deploySteps",
            []
        ):

            for phase in deploy_step.get(
                "releaseDeployPhases",
                []
            ):

                for job in phase.get(
                    "deploymentJobs",
                    []
                ):

                    for task in job.get(
                        "tasks",
                        []
                    ):

                        task_status = str(
                            task.get(
                                "status",
                                ""
                            )
                        ).lower()

                        if task_status != "failed":
                            continue

                        failed_task = task.get(
                            "name",
                            "Unknown"
                        )

                        issues = task.get(
                            "issues",
                            []
                        )

                        if issues:
                            error_message = issues[0].get(
                                "message",
                                "No error message available"
                            )

                        break

        # ==================================
        # Release URL
        # ==================================

        release_web_url = (
            f"https://dev.azure.com/"
            f"{ORG}/{PROJECT}"
            f"/_releaseProgress"
            f"?releaseId={release_id}"
        )

        # ==================================
        # Console Output
        # ==================================

        print("=" * 80)
        print(f"Release ID     : {release_id}")
        print(f"Release Name   : {release_name}")
        print(f"Release Created: {created_on}")
        print(f"Environment    : {environment_name}")
        print(f"Failed Task    : {failed_task}")
        print(f"Error          : {error_message}")
        print(f"URL            : {release_web_url}")
        print("=" * 80)

        # ==================================
        # Send To Datadog
        # ==================================

        payload = [
            {
                "ddsource": "azuredevops",
                "service": "pipeline-monitoring",

                "ddtags": (
                    f"release:{release_name},"
                    f"releaseid:{release_id},"
                    "type:release,"
                    "status:failed,"
                    "trigger:schedule,"
                    "source:azuredevops"
                ),

                "release_name": release_name,
                "release_id": str(release_id),
                "release_created": created_on,
                "environment": environment_name,
                "failed_task": failed_task,
                "error_message": error_message,
                "release_url": release_web_url,

                "message": (
                    f"Scheduled Release Failure\n\n"
                    f"Release: {release_name}\n"
                    f"Release ID: {release_id}\n"
                    f"Release Created: {created_on}\n"
                    f"Environment: {environment_name}\n"
                    f"Failed Task: {failed_task}\n\n"
                    f"Error:\n"
                    f"{error_message}\n\n"
                    f"Release URL:\n"
                    f"{release_web_url}"
                )
            }
        ]

        response = requests.post(
            "https://http-intake.logs.datadoghq.com/api/v2/logs",
            headers={
                "DD-API-KEY": DD_API_KEY,
                "Content-Type": "application/json"
            },
            json=payload
        )

        if response.status_code in [200, 202]:
            print(
                f"Successfully sent Datadog log "
                f"for release: {release_name}"
            )
        else:
            print(
                f"Failed sending Datadog log: "
                f"{response.status_code}"
            )
            print(response.text)


# ======================================
# Summary
# ======================================

print(
    f"\nFailed Scheduled Releases Found: "
    f"{failed_release_count}"
)

print("Release Monitoring Completed")