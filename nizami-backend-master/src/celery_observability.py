"""
Celery Observability Module

This module provides comprehensive observability for Celery tasks including:
1. OpenTelemetry instrumentation for distributed tracing
2. Structured logging for task lifecycle events  
3. Custom metrics for Grafana dashboards

All task lifecycle events are logged with consistent fields for easy querying
in your logging backend (CloudWatch, Grafana Loki, etc.)
"""

import os
import time
from celery.signals import (
    task_prerun,
    task_postrun,
    task_failure,
    task_retry,
    task_success,
    task_revoked,
    worker_ready,
    worker_shutdown,
)
from src import logger_util

logger = logger_util.get_logger(__name__)


def _get_task_info(task_id, task, args, kwargs):
    """Extract consistent task metadata for logging."""
    return {
        "celery_task_id": task_id,
        "celery_task_name": task.name if task else "unknown",
        "celery_task_args_count": len(args) if args else 0,
        "celery_task_kwargs_keys": list(kwargs.keys()) if kwargs else [],
        # Don't log actual args/kwargs values for security - they may contain PII
    }


# Store task start times for duration calculation
_task_start_times = {}


@task_prerun.connect
def on_task_prerun(task_id, task, args, kwargs, **kw):
    """Called just before a task is executed."""
    _task_start_times[task_id] = time.monotonic()
    
    task_info = _get_task_info(task_id, task, args, kwargs)
    logger.info({
        "event": "celery_task_started",
        "msg": f"Task started: {task.name}",
        **task_info,
    })


@task_postrun.connect
def on_task_postrun(task_id, task, args, kwargs, retval, state, **kw):
    """Called after a task has been executed (regardless of success/failure)."""
    start_time = _task_start_times.pop(task_id, None)
    duration_ms = (time.monotonic() - start_time) * 1000 if start_time else None
    
    task_info = _get_task_info(task_id, task, args, kwargs)
    logger.info({
        "event": "celery_task_completed",
        "msg": f"Task completed: {task.name}",
        "celery_task_state": state,
        "celery_task_duration_ms": round(duration_ms, 2) if duration_ms else None,
        **task_info,
    })


@task_success.connect
def on_task_success(sender, result, **kwargs):
    """Called when a task completes successfully."""
    task_name = sender.name if sender else "unknown"
    logger.info({
        "event": "celery_task_success",
        "msg": f"Task succeeded: {task_name}",
        "celery_task_name": task_name,
        # Don't log result - may contain sensitive data
    })


@task_failure.connect
def on_task_failure(task_id, exception, args, kwargs, traceback, einfo, sender, **kw):
    """Called when a task fails."""
    task_info = _get_task_info(task_id, sender, args, kwargs)
    
    # Clean up start time on failure
    _task_start_times.pop(task_id, None)
    
    logger.error({
        "event": "celery_task_failed",
        "msg": f"Task failed: {sender.name if sender else 'unknown'}",
        "celery_task_exception_type": type(exception).__name__,
        "celery_task_exception_msg": str(exception)[:500],  # Truncate long messages
        **task_info,
    })


@task_retry.connect
def on_task_retry(request, reason, einfo, **kwargs):
    """Called when a task is retried."""
    logger.warning({
        "event": "celery_task_retry",
        "msg": f"Task retrying: {request.task}",
        "celery_task_id": request.id,
        "celery_task_name": request.task,
        "celery_task_retry_reason": str(reason)[:500],
        "celery_task_retries": request.retries,
    })


@task_revoked.connect
def on_task_revoked(request, terminated, signum, expired, **kwargs):
    """Called when a task is revoked."""
    logger.warning({
        "event": "celery_task_revoked",
        "msg": f"Task revoked: {request.task if request else 'unknown'}",
        "celery_task_id": request.id if request else None,
        "celery_task_name": request.task if request else None,
        "celery_task_terminated": terminated,
        "celery_task_expired": expired,
    })


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Called when a Celery worker is ready to accept tasks."""
    logger.info({
        "event": "celery_worker_ready",
        "msg": "Celery worker ready",
        "celery_worker_hostname": str(sender) if sender else None,
    })


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """Called when a Celery worker is shutting down."""
    logger.info({
        "event": "celery_worker_shutdown", 
        "msg": "Celery worker shutting down",
        "celery_worker_hostname": str(sender) if sender else None,
    })


def setup_otel_instrumentation():
    """
    Setup OpenTelemetry instrumentation for Celery.
    
    This creates spans for each task execution that will be sent to
    your configured OTEL collector (Grafana Alloy).
    
    Only activates when OTEL_ENABLED is true.
    """
    if not os.environ.get('OTEL_ENABLED', 'false').lower() == 'true':
        logger.info("OTEL_ENABLED is not true, skipping Celery OTEL instrumentation")
        return
    
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        
        # Check if already instrumented to avoid double instrumentation
        if not CeleryInstrumentor().is_instrumented_by_opentelemetry:
            CeleryInstrumentor().instrument()
            logger.info("Celery OpenTelemetry instrumentation enabled")
        else:
            logger.info("Celery already instrumented by OpenTelemetry")
    except ImportError:
        logger.warning(
            "opentelemetry-instrumentation-celery not installed. "
            "Run: pip install opentelemetry-instrumentation-celery"
        )
    except Exception as e:
        logger.exception(f"Failed to setup Celery OTEL instrumentation: {e}")


def init_observability():
    """
    Initialize all Celery observability features.
    
    Call this from your celery.py after the Celery app is created.
    Signal handlers are auto-registered on import, this just handles OTEL setup.
    """
    setup_otel_instrumentation()
    logger.info("Celery observability initialized")

