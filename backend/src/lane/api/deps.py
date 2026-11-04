"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from lane.config import Settings, get_settings
from lane.db.base import get_session
from lane.storage.blobs import BlobStore, blob_store

# Closed when the endpoint returns, before the response is sent: a request holds one connection
# at a time, and the privacy pass that follows can always get one
SessionDep = Annotated[Session, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_blob_store(settings: SettingsDep) -> BlobStore:
    return blob_store(settings)


BlobStoreDep = Annotated[BlobStore, Depends(get_blob_store)]
