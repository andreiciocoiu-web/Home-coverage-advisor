"""Conector Google Drive (API oficiala v3) + sursa locala pentru teste/offline.

Acces cu privilegiu minim: scope `drive.readonly`. Aplicatia nu scrie, nu
muta si nu sterge nimic in Drive. Pentru un service account, folderul trebuie
partajat explicit cu adresa contului de serviciu (doar Viewer), ceea ce
limiteaza practic accesul la acel folder.
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_GDOC = "application/vnd.google-apps.document"
MIME_FOLDER = "application/vnd.google-apps.folder"
SUPPORTED_MIMES = {MIME_PDF, MIME_DOCX, MIME_GDOC}


@dataclass
class RemoteFile:
    external_id: str
    name: str
    mime: str
    modified_time: str
    checksum: str  # md5 Drive sau sha256 local; folosit pentru detectia modificarilor
    size: int
    revision: str = ""
    path: str = ""  # cale relativa in folder (informativa)


class SourceError(RuntimeError):
    pass


class LocalFolderSource:
    """Sursa locala cu aceeasi interfata ca DriveSource (teste, demo offline)."""

    kind = "local"

    def __init__(self, folder: str):
        self.root = Path(folder).expanduser().resolve()
        if not self.root.is_dir():
            raise SourceError(f"Folderul local nu exista: {self.root}")

    def list_files(self) -> list[RemoteFile]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file() or p.name.startswith((".", "~$")):
                continue
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if p.suffix.lower() == ".docx":
                mime = MIME_DOCX
            data = p.read_bytes()
            out.append(RemoteFile(
                external_id=str(p.relative_to(self.root)), name=p.name, mime=mime,
                modified_time=datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                checksum=hashlib.sha256(data).hexdigest(), size=len(data),
                path=str(p.relative_to(self.root)),
            ))
        return out

    def fetch(self, f: RemoteFile) -> bytes:
        return (self.root / f.external_id).read_bytes()


class DriveSource:
    kind = "drive"

    def __init__(self, folder_id: str):
        self.folder_id = folder_id.strip()
        if "/folders/" in self.folder_id:  # accepta si URL-ul complet al folderului
            self.folder_id = self.folder_id.split("/folders/")[1].split("?")[0].split("/")[0]
        self.service = self._build_service()

    # ---------------------------------------------------------------- autentificare
    @staticmethod
    def _credentials():
        mode = settings.drive_auth_mode
        cred_file = settings.google_credentials_file
        if not cred_file or not Path(cred_file).exists():
            raise SourceError(
                "Lipseste fisierul de credentiale Google (HCA_GOOGLE_CREDENTIALS). "
                "Vezi README, sectiunea 'Configurare Google Drive'.")
        if mode == "service_account":
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(cred_file, scopes=SCOPES)
        if mode == "oauth":
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            token_file = settings.google_token_file or str(settings.data_dir / "google_token.json")
            creds = None
            if Path(token_file).exists():
                creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            if not creds or not creds.valid:
                raise SourceError(
                    "Tokenul OAuth lipseste sau a expirat. Ruleaza: python -m scripts.google_oauth_login")
            return creds
        raise SourceError(f"Mod de autentificare necunoscut: {mode}")

    def _build_service(self):
        from googleapiclient.discovery import build
        return build("drive", "v3", credentials=self._credentials(), cache_discovery=False)

    # ---------------------------------------------------------------- listare
    def list_files(self) -> list[RemoteFile]:
        out: list[RemoteFile] = []
        self._walk(self.folder_id, "", out, depth=0)
        return out

    def _walk(self, folder_id: str, prefix: str, out: list, depth: int) -> None:
        if depth > 5:
            return
        token = None
        while True:
            resp = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id,name,mimeType,modifiedTime,md5Checksum,size,headRevisionId,version)",
                pageSize=200, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                if f["mimeType"] == MIME_FOLDER:
                    self._walk(f["id"], f"{prefix}{f['name']}/", out, depth + 1)
                    continue
                if f["mimeType"] not in SUPPORTED_MIMES:
                    out.append(RemoteFile(f["id"], f["name"], f["mimeType"], f.get("modifiedTime", ""),
                                          "", int(f.get("size", 0) or 0), path=prefix + f["name"]))
                    continue
                checksum = f.get("md5Checksum") or f"{f.get('version','')}-{f.get('modifiedTime','')}"
                out.append(RemoteFile(
                    external_id=f["id"], name=f["name"], mime=f["mimeType"],
                    modified_time=f.get("modifiedTime", ""), checksum=checksum,
                    size=int(f.get("size", 0) or 0),
                    revision=f.get("headRevisionId") or str(f.get("version", "")),
                    path=prefix + f["name"],
                ))
            token = resp.get("nextPageToken")
            if not token:
                break

    # ---------------------------------------------------------------- descarcare
    def fetch(self, f: RemoteFile) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload
        if f.mime == MIME_GDOC:
            req = self.service.files().export_media(fileId=f.external_id, mimeType=MIME_DOCX)
        else:
            req = self.service.files().get_media(fileId=f.external_id, supportsAllDrives=True)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()


def make_source(kind: str, location: str):
    if kind == "drive":
        return DriveSource(location)
    if kind == "local":
        return LocalFolderSource(location)
    raise SourceError(f"Tip de sursa necunoscut: {kind}")
