"""
Export & sharing views.

  POST /api/v1/chats/export          → { pdf_url, share_url }   (JWT required)
  GET  /api/v1/chats/exports/<uuid>  → JSON export data          (public)
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.http import Http404
from django.utils import timezone as dj_tz
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from src.chats.export_pdf import generate_pdf_bytes
from src.chats.models import ChatExport
from src.uploads.storage import get_s3_client, upload_bytes_to_s3

logger = logging.getLogger(__name__)

# Presigned URL TTL — must match expires_at offset set on ChatExport
_PRESIGNED_TTL_SECONDS = 7 * 24 * 3600
_PRESIGNED_TTL_DELTA = timedelta(days=7)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _export_s3_key(export_id: uuid.UUID) -> str:
    return f"exports/{export_id}/report.pdf"


def _upload_pdf_and_get_url(pdf_bytes: bytes, export_id: uuid.UUID) -> tuple[str, str]:
    """
    Upload PDF bytes to S3.
    Returns (s3_key, public_or_presigned_url).
    """
    bucket: str = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or ""
    if not bucket:
        raise RuntimeError("AWS_STORAGE_BUCKET_NAME is not configured.")

    key = _export_s3_key(export_id)
    upload_bytes_to_s3(bucket=bucket, key=key, body=pdf_bytes, content_type="application/pdf")

    # Replace internal Docker hostname with localhost so the presigned URL is
    # reachable from the browser in local development.
    endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", None) or ""
    if "localstack" in endpoint:
        endpoint = endpoint.replace("localstack", "localhost", 1)
    client = get_s3_client(endpoint_url=endpoint if endpoint else None)
    pdf_url: str = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": 'attachment; filename="chat-report.pdf"',
        },
        ExpiresIn=_PRESIGNED_TTL_SECONDS,
    )
    return key, pdf_url


# ---------------------------------------------------------------------------
# POST /api/v1/chats/export
# ---------------------------------------------------------------------------

class ExportChatView(APIView):
    """
    Generate a PDF from a chat transcript + summary, upload to S3,
    persist the export record, and return download + share URLs.

    Request body
    ------------
    {
        "chat": [
            { "role": "user"|"assistant", "content": "...", "timestamp": "<ISO>" }
        ],
        "summary": {
            "overview": "...",
            "problem": "...",
            "root_cause": "...",
            "solution": "...",
            "next_steps": ["...", "..."]
        },
        "chat_id": 123           // optional — links export to an existing Chat row
    }

    Response  200
    --------
    {
        "pdf_url": "https://...",
        "share_url": "https://yourapp.com/share/<uuid>"
    }
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = []

    def post(self, request: Request) -> Response:
        data: dict[str, Any] = request.data  # type: ignore[assignment]

        chat: list[dict] = data.get("chat") or []
        summary: dict = data.get("summary") or {}
        chat_id: int | None = data.get("chat_id")

        # ── Validate minimum input ──
        if not chat:
            return Response({"detail": "chat must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)
        required_summary_keys = {"overview", "problem", "root_cause", "solution", "next_steps"}
        missing = required_summary_keys - set(summary.keys())
        if missing:
            return Response(
                {"detail": f"summary is missing keys: {sorted(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Resolve optional Chat FK ──
        chat_obj = None
        if chat_id is not None:
            from src.chats.models import Chat
            try:
                chat_obj = Chat.objects.get(pk=chat_id, user=request.user)
            except Chat.DoesNotExist:
                return Response({"detail": "chat_id not found."}, status=status.HTTP_404_NOT_FOUND)

        # ── Generate PDF ──
        user_name = getattr(request.user, "first_name", None) or None
        try:
            pdf_bytes = generate_pdf_bytes(chat, summary, user_name=user_name)
        except RuntimeError as exc:
            logger.exception("PDF generation failed")
            return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # ── Build chat_json — enrich with citation metadata from DB when available ──
        # When chat_id is provided we pull metadata_json from the persisted Message rows
        # so that references remain clickable on the public share page.
        if chat_obj is not None:
            from src.chats.models import Message as _Message
            db_messages = list(
                _Message.objects.filter(chat=chat_obj)
                .order_by('created_at')
                .values('role', 'text', 'created_at', 'metadata_json')
            )
            chat_json_to_store: list[dict] = [
                {
                    'role': m['role'],
                    'content': m['text'],
                    'timestamp': m['created_at'].isoformat(),
                    **(({'metadata_json': m['metadata_json']} if m['metadata_json'] else {})),
                }
                for m in db_messages
            ]
        else:
            chat_json_to_store = chat

        # ── Upload to S3 ──
        export_id = uuid.uuid4()
        try:
            s3_key, pdf_url = _upload_pdf_and_get_url(pdf_bytes, export_id)
        except Exception as exc:
            logger.exception("S3 upload failed")
            return Response({"detail": f"Storage error: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # ── Persist record — expires_at mirrors the presigned URL TTL ──
        export = ChatExport.objects.create(
            id=export_id,
            chat=chat_obj,
            owner=request.user,
            chat_json=chat_json_to_store,
            summary_json=summary,
            pdf_s3_key=s3_key,
            pdf_url=pdf_url,
            expires_at=dj_tz.now() + _PRESIGNED_TTL_DELTA,
        )

        share_url = _build_share_url(request, export.id)

        return Response(
            {
                "pdf_url": _normalize_pdf_url(pdf_url),
                "share_url": share_url,
                "export_id": str(export.id),
            },
            status=status.HTTP_200_OK,
        )


def _build_share_url(request: Request, export_id: uuid.UUID) -> str:
    base = getattr(settings, "FRONTEND_DOMAIN", None) or request.build_absolute_uri("/")
    base = base.rstrip("/")
    return f"{base}/share/{export_id}"


def _normalize_pdf_url(url: str) -> str:
    """Replace internal Docker hostname with localhost so the URL works in a browser."""
    if url and "localstack" in url:
        return url.replace("localstack", "localhost", 1)
    return url


# ---------------------------------------------------------------------------
# GET /api/v1/chats/exports/<uuid>/  — public JSON API for Angular share page
# ---------------------------------------------------------------------------

class ShareExportApiView(APIView):
    """
    Public JSON endpoint — returns export data for the Angular share component.
    No authentication required; the UUID is the access token.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        try:
            export = ChatExport.objects.get(pk=pk)
        except ChatExport.DoesNotExist:
            raise Http404

        if export.is_expired:
            return Response(
                {"detail": "This share link has expired."},
                status=status.HTTP_410_GONE,
            )

        return Response({
            "export_id": str(export.id),
            "created_at": export.created_at.isoformat() if export.created_at else None,
            "chat": export.chat_json or [],
            "summary": export.summary_json or {},
            "pdf_url": _normalize_pdf_url(export.pdf_url or ""),
        })
