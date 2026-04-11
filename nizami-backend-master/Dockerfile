FROM python:3.12

ENV PYTHONIOENCODING=utf-8
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

# Install system dependencies required for Aspose.Words (ICU libraries for .NET globalization)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libicu-dev \
    icu-devtools \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

# Copy requirements first for better caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --default-timeout=100 --retries 10 -r requirements.txt

# Pre-download the Flashrank reranker model at build time so workers never
# download it at runtime (avoids the ~5s network hit and the race condition
# where multiple workers extract the same zip to /tmp simultaneously).
RUN python -c "from flashrank import Ranker; Ranker(model_name='ms-marco-MultiBERT-L-12', cache_dir='/app/models')"

# Copy the rest of the application
COPY . /app
#RUN python src/manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "src.wsgi:application"]
