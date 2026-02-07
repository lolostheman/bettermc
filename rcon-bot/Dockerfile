FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir mcron

COPY main.py .

CMD ["python", "-u", "main.py"]