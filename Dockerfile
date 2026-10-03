FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HOST=0.0.0.0 PORT=7860 FIRSTPR_REQUIRE_KEY=1 FIRSTPR_SECURE=1 FIRSTPR_DB=/tmp/firstpr.db
EXPOSE 7860
CMD ["python", "-m", "firstpr", "serve"]
