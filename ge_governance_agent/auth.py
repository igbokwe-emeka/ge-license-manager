import os
import google.auth
from google.oauth2 import service_account

def get_credentials(scopes: list[str], subject: str | None = None):
    """
    Load credentials using google.auth.default().
    
    If subject is provided and we are using a service account, apply delegation.
    If we are using user credentials, the subject is ignored.
    """
    credentials, _ = google.auth.default(scopes=scopes)
    
    if subject and isinstance(credentials, service_account.Credentials):
        return credentials.with_subject(subject)
    
    return credentials
