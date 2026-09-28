"""Authenticated access to this repo's Firestore database for Python scripts.

Firestore security rules only let the dashboard's allow-listed Google accounts read or write
(tightened 2026-09-28; before that shared_state / user_feedback were readable and writable by
anyone on the internet). Scripts authenticate as the Firebase project's service account instead
- service-account requests are authorized by IAM and bypass the security rules.

The key file lives OUTSIDE every repo, at ~/.secrets/<project-id>-sa.json (Firebase Console ->
Project settings -> Service accounts -> Generate new private key). Never commit it.
"""
import os

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

PROJECT_ID = "priya-jobs-dashboard"
KEY_PATH = os.path.join(os.path.expanduser("~"), ".secrets", f"{PROJECT_ID}-sa.json")
_SCOPES = ["https://www.googleapis.com/auth/datastore"]
_session = None


def session():
    """A requests.Session that attaches - and auto-refreshes - a service-account token."""
    global _session
    if _session is None:
        if not os.path.exists(KEY_PATH):
            raise FileNotFoundError(
                f"Firestore service-account key not found at {KEY_PATH}. Download it from Firebase "
                f"Console -> Project settings -> Service accounts -> Generate new private key.")
        creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=_SCOPES)
        _session = AuthorizedSession(creds)
    return _session
