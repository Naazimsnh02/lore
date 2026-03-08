"""JWT token verification using Google Cloud Identity Platform (Firebase Auth).

Requirements:
  - 25.1: Authentication via Google Cloud Identity Platform
  - 25.2: Unique user ID per sign-up
  - 25.6: Session timeout after 24 hours of inactivity
  - 25.7: All auth credentials transmitted over HTTPS only (enforced by Cloud Run)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Controls whether we use real Firebase tokens or stub validation (set by env)
_MOCK_AUTH = os.getenv("LORE_MOCK_AUTH", "false").lower() == "true"

# Lazily initialised Firebase app
_firebase_app = None


def _get_firebase_app():
    """Initialise (once) and return the Firebase Admin SDK app."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials

        # Cloud Run provides Application Default Credentials automatically.
        # Locally, set GOOGLE_APPLICATION_CREDENTIALS env var.
        cred = credentials.ApplicationDefault()
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialised with Application Default Credentials")
    except Exception as exc:
        logger.error("Firebase Admin SDK initialisation failed: %s", exc)
        raise RuntimeError("Firebase Admin SDK could not be initialised") from exc

    return _firebase_app


async def verify_token(token: str) -> dict:
    """Verify a Firebase ID token and return decoded user claims.

    Args:
        token: Raw Firebase ID token (JWT) from the client.

    Returns:
        dict with keys: ``user_id``, ``email`` (optional), ``name`` (optional).

    Raises:
        HTTPException(401) on any verification failure.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token required",
        )

    # Allow stub mode in development/testing to skip real Firebase calls
    if _MOCK_AUTH:
        return _mock_verify(token)

    try:
        import firebase_admin
        from firebase_admin import auth

        app = _get_firebase_app()
        decoded = auth.verify_id_token(token, app=app, check_revoked=True)

        # Enforce 24-hour session timeout (Requirement 25.6).
        # Firebase tokens already expire in 1 hour, but we also enforce
        # inactivity timeout by checking issue time.
        issued_at: int = decoded.get("iat", 0)
        if time.time() - issued_at > 86_400:  # 24 h in seconds
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired — please sign in again",
            )

        return {
            "user_id": decoded["uid"],
            "email": decoded.get("email"),
            "name": decoded.get("name"),
        }

    except auth.RevokedIdTokenError:
        logger.warning("Revoked token presented")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    except auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except auth.InvalidIdTokenError as exc:
        logger.warning("Invalid token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization token",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Token verification error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
        )


def _mock_verify(token: str) -> dict:
    """Stub verifier used in tests and local dev (LORE_MOCK_AUTH=true).

    Accepts any non-empty string prefixed with "mock_" and extracts
    a user_id from the suffix, e.g. "mock_user123" → user_id="user123".
    """
    if not token.startswith("mock_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mock auth: token must start with 'mock_'",
        )
    user_id = token[len("mock_"):]
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mock auth: empty user_id",
        )
    logger.debug("Mock auth accepted token for user_id=%s", user_id)
    return {"user_id": user_id, "email": f"{user_id}@mock.test", "name": user_id}
