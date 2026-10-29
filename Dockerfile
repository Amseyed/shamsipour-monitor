FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY monitor.py /app/monitor.py
CMD ["python", "-u", "/app/monitor.py"]
