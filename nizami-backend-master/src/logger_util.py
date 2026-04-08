import sys
import logging
import os
import random
from opentelemetry import trace
import uuid
from celery.utils.log import get_task_logger
import contextvars
from celery import current_task

IS_RUNNING_ON_CELERY = sys.argv and sys.argv[0].endswith('celery') and 'worker' in sys.argv
global_trace_id_var = contextvars.ContextVar("global_trace_id", default=None)
global_trace_parent_var = contextvars.ContextVar("global_trace_parent", default=None)

def get_logger(name):
    return get_task_logger(name) if IS_RUNNING_ON_CELERY else logging.getLogger(name)


def get_celery_trace_parent():
    if current_task and hasattr(current_task.request, 'traceparent') and current_task.request.traceparent:
        return current_task.request.traceparent
    return None

def get_context_trace_parent():
    current_span = trace.get_current_span()
    if current_span is not None:
        trace_id = hex(current_span.get_span_context().trace_id)[2:]
        if trace_id != '0':
            return extract_trace_parent_from_span_context(current_span.get_span_context())
    return None


def extract_trace_parent_from_span_context(span_context):
    return f"00-{span_context.trace_id}-{span_context.span_id}-{span_context.trace_flags}"

def get_trace_id():
    if not os.environ.get('OTEL_ENABLED', "false").lower() == "true":
        return None
    current_span = trace.get_current_span()
    if current_span is None:
        return get_or_create_global_trace_id()
    
    trace_context = current_span.get_span_context()
    trace_id_hex = hex(trace_context.trace_id)[2:]
    if trace_id_hex == '0':
        return get_or_create_global_trace_id()
    return trace_id_hex

def get_trace_parent():
    if current_task:
        # celery task, we need to use the traceparent from the task request
        return get_celery_trace_parent()
    # if not celery task, we need to use the traceparent from the context if it exists, otherwise we need to create a new one
    return get_context_trace_parent() or get_global_trace_parent()

def generate_custom_traceparent(trace_id):
    span_id = f"{random.randint(0, 2**64 - 1):016x}"
    trace_flags = "01" # 01 means trace is enabled
    traceparent = f"00-{trace_id}-{span_id}-{trace_flags}"
    return traceparent

def get_or_create_global_trace_id():
    global_trace_id = global_trace_id_var.get()
    if global_trace_id is None:
        global_trace_id = uuid.uuid4().hex
        global_trace_id_var.set(global_trace_id)
        global_trace_parent_var.set(generate_custom_traceparent(global_trace_id))
    return global_trace_id

def get_global_trace_parent():
    return global_trace_parent_var.get()

def clear_global_trace_id():
    global_trace_id_var.set(None)
    global_trace_parent_var.set(None)
