import json
from celery import Celery, Task
from django.conf import settings
from django.db import transaction
import os
import traceback
import uuid

from opentelemetry import trace
import logger_util


logger = logger_util.get_logger(__name__)


os.environ['DJANGO_SETTINGS_MODULE'] = "src.settings"

if getattr(settings, "Environment", "development").lower() == "production":
    app = Celery('tasks', broker=settings.REDIS_URL)
    app.config_from_object('django.conf:settings', namespace='CELERY')
    logger.info('Celery: PROD detected. Using SQS config')
elif settings.TESTING:
    app = Celery('tasks', broker="memory://")
    app.conf.update(
    task_always_eager=settings.TESTING,  # Run tasks synchronously in testing
    task_eager_propagates=False,  # Don't propagate exceptions in testing so on_failure can handle them
    task_ignore_result=False,  
    task_track_started=True,  
    task_acks_late=True,  # Acknowledge tasks after they're completed
    task_reject_on_worker_lost=True,
)
    logger.info('Celery: TESTING detected. Using in-memory queue and cache backend')

# Initialize Celery observability (OTEL tracing + structured logging)
# Signal handlers are auto-registered on import, OTEL setup is explicit
from celery_observability import init_observability
init_observability()


@app.task
def error_handler(*args, **kwargs):
    logger.exception(dict(fire_traceback=kwargs['fire_traceback']))


class BaseTask(Task):
    def delay(self, *args, **kwargs):
        fire_traceback = ''.join(traceback.format_stack())
        trace_id = logger_util.get_trace_id() or uuid.uuid4()
        traceparent = logger_util.get_context_trace_parent() or logger_util.get_global_trace_parent() # first try to get the traceparent from the context, if not found, get the traceparent from the global trace parent
        task_id = f'tid-{trace_id}-{uuid.uuid4()}'[:45]
        return super().apply_async(link_error=error_handler.s(fire_traceback=fire_traceback), 
                                   args=args, task_id=task_id, kwargs=kwargs, traceparent=traceparent)

    def on_db_commit_delay(self, *args, **kwargs): # May be changed to delay once tested to affect all celery tasks + need to rename above fct
        return transaction.on_commit(lambda: self.delay(*args, **kwargs))


def async_task(*dargs, **dkwargs):
    return app.task(*dargs, base=BaseTask, **dkwargs)


@async_task
def test_task():
    logger.info("Executing task logic...")

@async_task
def fail_test_task():
    raise Exception("fail task test...")

@async_task
def simple_test_celery(x, y, raise_error=False):
    try:
        logger.info(json.dumps(dict(msg=f'Test from celery async task', x=x, y=y), default=str))
        j = x / y
    except Exception as ex:
        logger.exception(json.dumps(dict(msg=f'Error from celery', error=str(ex)), default=str))
        if raise_error:
            raise ex
