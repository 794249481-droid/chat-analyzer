Warning: fs was declared with const; use let for reassignable variables.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
RUN mkdir -p uploads output

EXPOSE 8888

CMD ["python3", "server.py"]
