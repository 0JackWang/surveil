FROM python:3.12-slim
WORKDIR /app
COPY server.py .
# Create data directory for persistent storage
RUN mkdir -p /data
ENV DATA_FILE=/data/snapshots.json
ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
