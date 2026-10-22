FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY portal ./portal
COPY alembic ./alembic
RUN pip install --no-cache-dir . && useradd --system --uid 10001 portal
USER portal
EXPOSE 8000

# Migra y arranca. Tras nginx: --proxy-headers para la IP real del papá (límites por IP).
# '*' es seguro sólo porque el puerto se publica en 127.0.0.1 y nginx SOBRESCRIBE
# X-Forwarded-For (uvicorn toma el primer valor): ver deploy/nginx.conf.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn portal.app:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers 2"]
