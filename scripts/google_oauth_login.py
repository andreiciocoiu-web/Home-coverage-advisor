"""Autentificare OAuth (mod `oauth`) pentru acces doar-citire la Google Drive.

Necesita un client OAuth de tip "Desktop app" descarcat ca JSON in HCA_GOOGLE_CREDENTIALS.
Utilizare: python -m scripts.google_oauth_login
"""
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from advisor.config import settings
from advisor.drive import SCOPES

if __name__ == "__main__":
    flow = InstalledAppFlow.from_client_secrets_file(settings.google_credentials_file, SCOPES)
    creds = flow.run_local_server(port=0)
    token_file = Path(settings.google_token_file or settings.data_dir / "google_token.json")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_file, 0o600)
    print(f"Token salvat in {token_file} (scope: {', '.join(SCOPES)})")
