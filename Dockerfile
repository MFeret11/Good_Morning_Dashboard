FROM python:3.12-slim

# Unbuffered so print() output shows up in `docker logs` right away.
# The commute windows and the afternoon push use local time; override TZ with
# `-e TZ=...` if you run this elsewhere.
ENV PYTHONUNBUFFERED=1 \
    TZ=America/New_York

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
