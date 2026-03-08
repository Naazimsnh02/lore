"""Media Store Manager – Cloud Storage-backed media persistence for LORE.

Design reference: LORE design.md, Section 11 – Media Store Manager.
Requirements: 22.1 – 22.7.

Architecture notes
------------------
- All I/O is async.  The Cloud Storage Python client exposes a synchronous
  API, so every GCS call is wrapped in ``asyncio.get_event_loop().run_in_executor``
  to avoid blocking the event loop.
- Media metadata (mediaId → GCS object name, size, timestamps) is mirrored
  into Firestore under the ``media_records/{mediaId}`` collection so that
  quota queries and cleanup operations can be performed efficiently without
  listing GCS objects.
- Signed URLs are generated with the ``google.auth`` / ``google.cloud.storage``
  RSA signing flow and carry a configurable expiry (default 60 minutes).
- Data encryption: Cloud Storage automatically encrypts data at rest with
  Google-managed keys (AES-256).  All traffic travels over TLS 1.3, satisfying
  Requirement 22.1 and the general security requirements.
- For unit/property tests the constructor accepts mock clients so no real GCP
  credentials are required.

GCS path convention:
    media/{userId}/{sessionId}/{mediaType}/{mediaId}.{ext}

Firestore collection:
    media_records/{mediaId}

Firestore quota-aggregate document (updated atomically):
    media_quotas/{userId}
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import mimetypes
import time
import uuid
from typing import Any, Optional

from .models import (
    MediaFile,
    MediaMetadata,
    MediaStatus,
    MediaType,
    QuotaInfo,
    StoredMediaRecord,
)

logger = logging.getLogger(__name__)

# How long to retain media before it becomes eligible for automated cleanup.
_DEFAULT_RETENTION_DAYS = 90

# Firestore collection names
_MEDIA_RECORDS_COLLECTION = "media_records"
_MEDIA_QUOTAS_COLLECTION = "media_quotas"

# Default per-user storage quota (10 GiB)
_DEFAULT_QUOTA_BYTES = 10 * 1024 * 1024 * 1024

# Default signed-URL expiry (60 minutes)
_DEFAULT_SIGNED_URL_EXPIRY_MINUTES = 60


# ── Custom exceptions ──────────────────────────────────────────────────────────


class MediaStoreError(Exception):
    """Base exception for all MediaStoreManager errors."""


class MediaNotFoundError(MediaStoreError):
    """Raised when a requested media ID does not exist."""


class QuotaExceededError(MediaStoreError):
    """Raised when storing media would exceed the user's quota."""

    def __init__(self, user_id: str, quota: QuotaInfo) -> None:
        self.user_id = user_id
        self.quota = quota
        super().__init__(
            f"Storage quota exceeded for user {user_id}: "
            f"{quota.used_mb:.1f} MB used / {quota.limit_mb:.1f} MB limit"
        )


# ── Manager ───────────────────────────────────────────────────────────────────


class MediaStoreManager:
    """Manages storage and retrieval of generated videos and illustrations.

    Parameters
    ----------
    storage_client:
        A ``google.cloud.storage.Client`` (or compatible mock/fake).
        If ``None`` the manager will attempt to create a default client using
        Application Default Credentials.  Pass a mock/stub in tests.
    firestore_client:
        A ``google.cloud.firestore.Client`` (or compatible mock).
        If ``None`` a default client is created.
    bucket_name:
        Cloud Storage bucket for media files.
    project_id:
        GCP project ID.  Used when constructing default clients.
    quota_bytes:
        Per-user storage quota in bytes.  Defaults to 10 GiB.
    """

    def __init__(
        self,
        storage_client: Any = None,
        firestore_client: Any = None,
        bucket_name: str = "lore-media-store",
        project_id: str | None = None,
        quota_bytes: int = _DEFAULT_QUOTA_BYTES,
    ) -> None:
        self._bucket_name = bucket_name
        self._quota_bytes = quota_bytes
        self._loop: asyncio.AbstractEventLoop | None = None

        if storage_client is not None:
            self._gcs = storage_client
        else:
            from google.cloud import storage  # type: ignore

            self._gcs = storage.Client(project=project_id)

        if firestore_client is not None:
            self._db = firestore_client
        else:
            from google.cloud import firestore  # type: ignore

            self._db = firestore.Client(project=project_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Return the running event loop (cached per manager instance)."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop

    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous callable in the thread-pool executor."""
        loop = self._get_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _build_gcs_object_name(
        self,
        user_id: str,
        session_id: str,
        media_type: MediaType,
        media_id: str,
        extension: str,
    ) -> str:
        """Return the GCS object path for the given media item."""
        ext = extension.lstrip(".")
        return f"media/{user_id}/{session_id}/{media_type.value}/{media_id}.{ext}"

    def _infer_extension(self, mime_type: str, media_type: MediaType) -> str:
        """Infer a file extension from the MIME type, with sensible defaults."""
        ext = mimetypes.guess_extension(mime_type)
        if ext:
            # mimetypes sometimes returns .jpe instead of .jpg
            ext = ext.lstrip(".")
            if ext == "jpe":
                ext = "jpg"
            return ext
        # Fall back to per-type defaults
        defaults = {
            MediaType.VIDEO: "mp4",
            MediaType.ILLUSTRATION: "jpg",
            MediaType.NARRATION: "mp3",
            MediaType.CHRONICLE: "pdf",
        }
        return defaults.get(media_type, "bin")

    # ── Public API ────────────────────────────────────────────────────────────

    async def store_media(
        self,
        media: MediaFile,
        user_id: str,
        session_id: str,
    ) -> str:
        """Store a media file in Cloud Storage and record its metadata.

        Requirements: 22.1 – 22.3, 22.5, 22.6.

        Parameters
        ----------
        media:
            MediaFile object with ``data`` bytes populated.
        user_id:
            Owner user ID (used for path namespacing and quota tracking).
        session_id:
            Session that produced this media (used for path namespacing).

        Returns
        -------
        str
            The media ID (UUID) that can be passed to ``retrieve_media``,
            ``generate_signed_url``, or ``delete_media``.

        Raises
        ------
        QuotaExceededError
            If storing the file would exceed the user's storage quota.
        MediaStoreError
            On any GCS or Firestore error.
        """
        if media.data is None:
            raise MediaStoreError("MediaFile.data must be populated for store_media.")

        # Check quota before writing (Requirement 22.6)
        quota = await self.get_user_quota(user_id)
        file_size = len(media.data)
        if quota.used_bytes + file_size > quota.limit_bytes:
            logger.warning(
                "Quota exceeded for user %s: %d bytes used, %d bytes limit, %d bytes requested",
                user_id,
                quota.used_bytes,
                quota.limit_bytes,
                file_size,
            )
            raise QuotaExceededError(user_id, quota)

        media_id = media.id or str(uuid.uuid4())
        extension = self._infer_extension(media.mime_type, media.media_type)
        gcs_object_name = self._build_gcs_object_name(
            user_id, session_id, media.media_type, media_id, extension
        )

        # ── Upload to Cloud Storage ───────────────────────────────────────────
        def _upload():
            bucket = self._gcs.bucket(self._bucket_name)
            blob = bucket.blob(gcs_object_name)
            blob.upload_from_string(
                media.data,
                content_type=media.mime_type,
                # Attach lightweight metadata to the GCS object itself
                client=self._gcs,
            )
            blob.metadata = {
                "user_id": user_id,
                "session_id": session_id,
                "media_type": media.media_type.value,
                "media_id": media_id,
            }
            blob.patch()

        try:
            await self._run_sync(_upload)
        except Exception as exc:
            logger.error("GCS upload failed for media %s: %s", media_id, exc)
            raise MediaStoreError(f"Failed to upload media {media_id}: {exc}") from exc

        # ── Write metadata record to Firestore ────────────────────────────────
        retention_ms = int(
            (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=_DEFAULT_RETENTION_DAYS)
            ).timestamp()
            * 1000
        )
        record = StoredMediaRecord(
            media_id=media_id,
            user_id=user_id,
            session_id=session_id,
            media_type=media.media_type,
            gcs_object_name=gcs_object_name,
            mime_type=media.mime_type,
            size_bytes=file_size,
            created_at_ms=int(time.time() * 1000),
            expires_at_ms=retention_ms,
            status=MediaStatus.ACTIVE,
            description=media.metadata.description,
            extra=media.metadata.extra,
        )

        def _write_record():
            self._db.collection(_MEDIA_RECORDS_COLLECTION).document(media_id).set(
                record.to_firestore_dict()
            )

        try:
            await self._run_sync(_write_record)
        except Exception as exc:
            logger.error(
                "Firestore write failed for media record %s: %s", media_id, exc
            )
            raise MediaStoreError(
                f"Failed to write metadata for media {media_id}: {exc}"
            ) from exc

        # ── Update user quota aggregate ───────────────────────────────────────
        await self._increment_quota(user_id, delta_bytes=file_size, delta_count=1)

        logger.info(
            "Stored media %s for user=%s session=%s type=%s size=%d bytes",
            media_id,
            user_id,
            session_id,
            media.media_type.value,
            file_size,
        )
        return media_id

    async def retrieve_media(self, media_id: str) -> MediaFile:
        """Download a media file from Cloud Storage.

        Requirements: 22.2, 22.7 (< 500ms latency for 95% of requests).

        Parameters
        ----------
        media_id:
            The ID returned by ``store_media``.

        Returns
        -------
        MediaFile
            MediaFile with ``data`` bytes populated.

        Raises
        ------
        MediaNotFoundError
            If no media with the given ID exists.
        MediaStoreError
            On any GCS or Firestore error.
        """
        # ── Load metadata from Firestore ──────────────────────────────────────
        def _load_record():
            doc = (
                self._db.collection(_MEDIA_RECORDS_COLLECTION)
                .document(media_id)
                .get()
            )
            if not doc.exists:
                return None
            return doc.to_dict()

        try:
            raw = await self._run_sync(_load_record)
        except Exception as exc:
            logger.error("Firestore read failed for media %s: %s", media_id, exc)
            raise MediaStoreError(
                f"Failed to read metadata for media {media_id}: {exc}"
            ) from exc

        if raw is None:
            raise MediaNotFoundError(f"Media not found: {media_id}")

        record = StoredMediaRecord.from_firestore_dict(raw)

        if record.status == MediaStatus.DELETED:
            raise MediaNotFoundError(f"Media {media_id} has been deleted.")

        # ── Download bytes from GCS ───────────────────────────────────────────
        def _download():
            bucket = self._gcs.bucket(self._bucket_name)
            blob = bucket.blob(record.gcs_object_name)
            return blob.download_as_bytes()

        try:
            data: bytes = await self._run_sync(_download)
        except Exception as exc:
            logger.error("GCS download failed for media %s: %s", media_id, exc)
            raise MediaStoreError(
                f"Failed to download media {media_id}: {exc}"
            ) from exc

        media = MediaFile(
            id=media_id,
            media_type=record.media_type,
            data=data,
            mime_type=record.mime_type,
            size=record.size_bytes,
            metadata=MediaMetadata(
                user_id=record.user_id,
                session_id=record.session_id,
                media_type=record.media_type,
                created_at_ms=record.created_at_ms,
                gcs_object_name=record.gcs_object_name,
                description=record.description,
                extra=record.extra,
            ),
            status=record.status,
        )
        logger.debug("Retrieved media %s (%d bytes)", media_id, len(data))
        return media

    async def delete_media(self, media_id: str) -> None:
        """Soft-delete a media file (marks as deleted; does not free GCS storage
        immediately – use ``cleanupOldMedia`` for hard deletion).

        Requirements: 22.2.

        Raises
        ------
        MediaNotFoundError
            If no media with the given ID exists.
        """
        def _load_record():
            doc = (
                self._db.collection(_MEDIA_RECORDS_COLLECTION)
                .document(media_id)
                .get()
            )
            if not doc.exists:
                return None
            return doc.to_dict()

        raw = await self._run_sync(_load_record)
        if raw is None:
            raise MediaNotFoundError(f"Media not found: {media_id}")

        record = StoredMediaRecord.from_firestore_dict(raw)
        if record.status == MediaStatus.DELETED:
            logger.debug("Media %s already deleted; skipping.", media_id)
            return

        # Mark as deleted in Firestore
        def _mark_deleted():
            self._db.collection(_MEDIA_RECORDS_COLLECTION).document(media_id).update(
                {"status": MediaStatus.DELETED.value}
            )

        await self._run_sync(_mark_deleted)

        # Decrement quota aggregate
        await self._increment_quota(
            record.user_id, delta_bytes=-record.size_bytes, delta_count=-1
        )

        logger.info("Soft-deleted media %s for user %s", media_id, record.user_id)

    async def generate_signed_url(
        self,
        media_id: str,
        expiration_minutes: int = _DEFAULT_SIGNED_URL_EXPIRY_MINUTES,
    ) -> str:
        """Generate a time-limited signed URL for direct client download.

        Requirements: 22.4.

        Parameters
        ----------
        media_id:
            The media to generate a URL for.
        expiration_minutes:
            How long the URL remains valid.  Defaults to 60 minutes.

        Returns
        -------
        str
            HTTPS signed URL.

        Raises
        ------
        MediaNotFoundError
            If no media with the given ID exists.
        """
        def _load_record():
            doc = (
                self._db.collection(_MEDIA_RECORDS_COLLECTION)
                .document(media_id)
                .get()
            )
            if not doc.exists:
                return None
            return doc.to_dict()

        raw = await self._run_sync(_load_record)
        if raw is None:
            raise MediaNotFoundError(f"Media not found: {media_id}")

        record = StoredMediaRecord.from_firestore_dict(raw)
        if record.status == MediaStatus.DELETED:
            raise MediaNotFoundError(f"Media {media_id} has been deleted.")

        def _sign():
            bucket = self._gcs.bucket(self._bucket_name)
            blob = bucket.blob(record.gcs_object_name)
            return blob.generate_signed_url(
                expiration=datetime.timedelta(minutes=expiration_minutes),
                method="GET",
                version="v4",
            )

        try:
            url: str = await self._run_sync(_sign)
        except Exception as exc:
            logger.error(
                "Signed URL generation failed for media %s: %s", media_id, exc
            )
            raise MediaStoreError(
                f"Failed to generate signed URL for media {media_id}: {exc}"
            ) from exc

        logger.debug(
            "Generated signed URL for media %s (expires in %d min)",
            media_id,
            expiration_minutes,
        )
        return url

    async def get_user_quota(self, user_id: str) -> QuotaInfo:
        """Return current storage usage for a user.

        Requirements: 22.6.

        The quota aggregate is maintained in Firestore under
        ``media_quotas/{userId}`` for O(1) reads.
        """
        def _load():
            doc = (
                self._db.collection(_MEDIA_QUOTAS_COLLECTION)
                .document(user_id)
                .get()
            )
            if not doc.exists:
                return None
            return doc.to_dict()

        try:
            raw = await self._run_sync(_load)
        except Exception as exc:
            logger.error("Failed to load quota for user %s: %s", user_id, exc)
            raise MediaStoreError(
                f"Failed to load quota for user {user_id}: {exc}"
            ) from exc

        if raw is None:
            return QuotaInfo(
                user_id=user_id,
                used_bytes=0,
                limit_bytes=self._quota_bytes,
                file_count=0,
            )

        return QuotaInfo(
            user_id=user_id,
            used_bytes=raw.get("used_bytes", 0),
            limit_bytes=raw.get("limit_bytes", self._quota_bytes),
            file_count=raw.get("file_count", 0),
        )

    async def cleanup_old_media(self, user_id: str, older_than_days: int) -> int:
        """Hard-delete GCS objects and Firestore records older than a threshold.

        Requirements: 22.5 (90-day retention), 22.6 (cleanup option).

        Parameters
        ----------
        user_id:
            The user whose media should be cleaned.
        older_than_days:
            Delete media whose ``created_at_ms`` is older than this many days.

        Returns
        -------
        int
            Number of media files permanently deleted.
        """
        cutoff_ms = int(
            (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=older_than_days)
            ).timestamp()
            * 1000
        )

        # ── Query Firestore for eligible records ──────────────────────────────
        def _query_old_records():
            return list(
                self._db.collection(_MEDIA_RECORDS_COLLECTION)
                .where("user_id", "==", user_id)
                .where("created_at_ms", "<", cutoff_ms)
                .where("status", "!=", MediaStatus.DELETED.value)
                .stream()
            )

        try:
            docs = await self._run_sync(_query_old_records)
        except Exception as exc:
            logger.error(
                "Firestore query failed during cleanup for user %s: %s", user_id, exc
            )
            raise MediaStoreError(f"Cleanup query failed for user {user_id}: {exc}") from exc

        deleted_count = 0
        total_bytes_freed = 0

        for doc in docs:
            raw = doc.to_dict()
            record = StoredMediaRecord.from_firestore_dict(raw)

            # ── Delete from GCS ───────────────────────────────────────────────
            def _delete_blob(obj_name=record.gcs_object_name):
                bucket = self._gcs.bucket(self._bucket_name)
                blob = bucket.blob(obj_name)
                blob.delete()

            try:
                await self._run_sync(_delete_blob)
            except Exception as exc:
                # Log and continue – partial failures are acceptable
                logger.warning(
                    "GCS delete failed for object %s (media %s): %s",
                    record.gcs_object_name,
                    record.media_id,
                    exc,
                )
                continue

            # ── Mark as deleted in Firestore ──────────────────────────────────
            def _mark_deleted(mid=record.media_id):
                self._db.collection(_MEDIA_RECORDS_COLLECTION).document(mid).update(
                    {"status": MediaStatus.DELETED.value}
                )

            try:
                await self._run_sync(_mark_deleted)
            except Exception as exc:
                logger.warning(
                    "Firestore update failed for media %s during cleanup: %s",
                    record.media_id,
                    exc,
                )

            total_bytes_freed += record.size_bytes
            deleted_count += 1

        if deleted_count > 0:
            await self._increment_quota(
                user_id,
                delta_bytes=-total_bytes_freed,
                delta_count=-deleted_count,
            )
            logger.info(
                "Cleanup: deleted %d media files (%d bytes freed) for user %s",
                deleted_count,
                total_bytes_freed,
                user_id,
            )

        return deleted_count

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _increment_quota(
        self, user_id: str, delta_bytes: int, delta_count: int
    ) -> None:
        """Atomically update the quota aggregate document for a user."""
        from google.cloud.firestore_v1 import Increment  # type: ignore

        def _update():
            quota_ref = (
                self._db.collection(_MEDIA_QUOTAS_COLLECTION).document(user_id)
            )
            quota_ref.set(
                {
                    "used_bytes": Increment(delta_bytes),
                    "limit_bytes": self._quota_bytes,
                    "file_count": Increment(delta_count),
                },
                merge=True,
            )

        try:
            await self._run_sync(_update)
        except Exception as exc:
            # Non-fatal – quota may be temporarily stale but media is stored
            logger.warning(
                "Failed to update quota for user %s (delta=%d bytes): %s",
                user_id,
                delta_bytes,
                exc,
            )
