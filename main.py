import requests
import os
import json
import re
from pathlib import Path
import html2text
from http.server import BaseHTTPRequestHandler


# List of repositories to monitor (owner/repo format)
REPOS = [
    "nestjs/nest",
    "slackapi/slack-github-action",
    "microsoft/typescript",
    # Add more repositories as needed
]
GITHUB_ACCESS_TOKEN = os.getenv(
    "GITHUB_ACCESS_TOKEN",
)

# Get the Slack webhook URL from environment variable or use a default value
# Make sure the URL is properly formatted (not escaped)
SLACK_WEBHOOK_RAW = os.getenv(
    "SLACK_WEBHOOK_URL",
    "https://hooks.slack.com/services/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",
)

# Ensure the URL is not escaped by removing any escape sequences
SLACK_WEBHOOK = SLACK_WEBHOOK_RAW.replace("\\x3a", ":")

# GitHub Gist configuration
GIST_ID = os.getenv("GIST_ID", "")  # Set this to your Gist ID
GIST_FILENAME = "github_releases_data.json"


def load_last_releases():
    """Load the last known release IDs from GitHub Gist"""
    if not GIST_ID:
        print("⚠️ No GIST_ID provided. Using an empty dictionary for release tracking.")
        return {}

    try:
        # Fetch the gist content
        headers = {
            "Authorization": f"Bearer {GITHUB_ACCESS_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        response = requests.get(
            f"https://api.github.com/gists/{GIST_ID}", headers=headers
        )
        response.raise_for_status()

        gist_data = response.json()
        if GIST_FILENAME in gist_data["files"]:
            content = gist_data["files"][GIST_FILENAME]["content"]
            return json.loads(content)
        else:
            print(f"⚠️ File {GIST_FILENAME} not found in Gist. Using empty dictionary.")
            return {}
    except Exception as e:
        print(f"⚠️ Error loading releases from Gist: {str(e)}. Using empty dictionary.")
        return {}


def save_last_release(releases_data):
    """Save the last release IDs to GitHub Gist"""
    if not GIST_ID:
        print("⚠️ No GIST_ID provided. Release data not saved.")
        return

    try:
        # Prepare the gist update payload
        headers = {
            "Authorization": f"Bearer {GITHUB_ACCESS_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        payload = {
            "files": {GIST_FILENAME: {"content": json.dumps(releases_data, indent=2)}}
        }

        # Update the gist
        response = requests.patch(
            f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload
        )
        response.raise_for_status()
        print(f"✅ Successfully updated release data in Gist ({GIST_ID})")
    except Exception as e:
        print(f"❌ Error saving releases to Gist: {str(e)}")


def github_to_slack_markdown(text):
    """Convert GitHub markdown to Slack-compatible markdown using GitHub API"""
    if not text:
        return "No release notes provided."

    # Limit text length to avoid GitHub API and Slack message limits
    if len(text) > 4000:  # GitHub API has a limit too
        text = text[:4000] + "... (truncated)"

    # First, convert markdown to HTML using GitHub's API
    try:
        response = requests.post(
            "https://api.github.com/markdown",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {GITHUB_ACCESS_TOKEN}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"text": text},
        )
        response.raise_for_status()
        html_content = response.text

        # Convert HTML to Slack-friendly markdown
        h = html2text.HTML2Text()
        h.ignore_images = True  # Slack doesn't render markdown images well
        h.body_width = 0  # Don't wrap text
        h.ignore_tables = False
        h.mark_code = True

        slack_markdown = h.handle(html_content)

        # Post-processing for Slack compatibility
        # Convert <a href="url">text</a> style links to Slack <url|text> format
        slack_markdown = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", slack_markdown)

        # Ensure headers are bold
        slack_markdown = re.sub(
            r"^#{1,6}\s+(.+)$", r"*\1*", slack_markdown, flags=re.MULTILINE
        )

        # Limit final text length for Slack
        if len(slack_markdown) > 2900:
            slack_markdown = slack_markdown[:2900] + "... (truncated)"

        return slack_markdown

    except Exception as e:
        # Fallback to simple conversion if GitHub API fails
        print(f"⚠️ GitHub markdown API failed: {str(e)}. Using simple conversion.")

        # Simple fallback conversion
        # Convert GitHub links [text](url) to Slack links <url|text>
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

        # Convert headers to bold text
        text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

        # Handle code blocks
        text = re.sub(r"```[a-z]*\n", r"```\n", text)

        # Replace HTML-style entities
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

        # Fix bullet points spacing
        text = re.sub(r"^(\s*[-*]\s+)", r"\n\1", text, flags=re.MULTILINE)

        return text


def main():
    last_releases = load_last_releases()

    for repo in REPOS:
        try:
            # Fetch latest release for this repository
            response = requests.get(
                f"https://api.github.com/repos/{repo}/releases/latest"
            )
            response.raise_for_status()
            latest_release = response.json()

            # Fetch repository info to get the repository avatar
            repo_response = requests.get(f"https://api.github.com/repos/{repo}")
            repo_response.raise_for_status()
            repo_info = repo_response.json()

            # Check if this is a new release
            if (
                repo not in last_releases
                or str(latest_release["id"]) != last_releases[repo]
            ):
                # Format release notes for Slack
                release_notes = github_to_slack_markdown(latest_release["body"])

                # Send to Slack
                slack_payload = {
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"🚀 *New {repo} Release: {latest_release['name']}*",
                            },
                            "accessory": {
                                "type": "image",
                                "image_url": repo_info["owner"]["avatar_url"],
                                "alt_text": "Repository Avatar",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Release Notes:*\n{release_notes}\n\n<{latest_release['html_url']}|View Release>",
                            },
                        },
                    ]
                }
                response = requests.post(SLACK_WEBHOOK, json=slack_payload)
                response.raise_for_status()
                print(
                    f"✅ Notification sent for {repo} release {latest_release['name']}"
                )

                # Update last release ID for this repository
                last_releases[repo] = str(latest_release["id"])

            else:
                print(f"No new releases for {repo}")

        except requests.RequestException as e:
            print(f"❌ Error checking {repo}: {str(e)}")

    # Save all release IDs at once
    save_last_release(last_releases)


def handle_request(request):
    """Handler for serverless function requests"""
    main()
    return {
        "statusCode": 200,
        "body": json.dumps({"message": "GitHub releases check completed successfully"}),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests for Vercel serverless function"""
        result = handle_request(self)
        self.send_response(result["statusCode"])
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(result["body"].encode())
        return


if __name__ == "__main__":
    main()
