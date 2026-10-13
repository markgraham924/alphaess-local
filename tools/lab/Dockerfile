FROM python:3.13-slim
WORKDIR /app
COPY model.py server.py ./
USER 65534:65534
EXPOSE 1502 1503
CMD ["python", "server.py", "--host", "0.0.0.0"]
