bind = "0.0.0.0:8000"
workers = 1
worker_class = "gthread"
threads = 4          # 2 workers × 4 threads = 8 concurrent slots
timeout = 300          # LLM chains can be slow; must be > ALB idle_timeout (120s) with headroom
graceful_timeout = 120 # give in-flight requests time to finish; raise ECS stopTimeout to match if deploying long-running LLM calls
keepalive = 5
preload_app = True     # load Django app once in master; workers inherit via fork (COW-shared pages = lower total RSS)
accesslog = "-"        # stdout → CloudWatch
errorlog = "-"         # stdout → CloudWatch
loglevel = "info"


def post_fork(server, worker):
    """Pre-warm the Flashrank reranker model in each worker process.

    FlashrankRerank downloads ~99 MB on first instantiation and caches it.
    Doing this here means the download completes during startup, not during
    the first user request, so no request ever hits the cold-download penalty.
    """
    import logging
    log = logging.getLogger("gunicorn.error")
    try:
        from src.chats.retrieval.reranker import get_reranker
        get_reranker(top_n=6)
        log.info("[worker %s] Flashrank reranker pre-warmed", worker.pid)
    except Exception as exc:
        log.warning("[worker %s] Flashrank pre-warm failed: %s", worker.pid, exc)
