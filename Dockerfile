FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-llm.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-llm.txt
COPY task_agent ./task_agent
COPY demo ./demo
COPY sample_inbox ./sample_inbox
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "task_agent", "watch"]
