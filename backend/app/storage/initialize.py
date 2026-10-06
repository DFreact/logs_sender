from app.settings import Settings
from app.storage.files import BlobStore

if __name__ == "__main__":
    try:
        settings = Settings()
        BlobStore(settings.storage_root, settings.blob_max_bytes).initialize()
    except Exception:
        raise SystemExit("STORAGE_INITIALIZATION_FAILED") from None
