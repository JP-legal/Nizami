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

# Python 3.12+ does not ship pkg_resources; django-q needs it. Install setuptools before any app deps.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "setuptools==82.0.1"

# Copy requirements first for better caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt

# Copy the rest of the application
COPY . /app
#RUN python src/manage.py collectstatic --noinput

EXPOSE 8000

STOPSIGNAL SIGINT

CMD ["gunicorn", "--config", "gunicorn.conf.py", "src.wsgi:application"]
