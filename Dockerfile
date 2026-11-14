FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app
WORKDIR /app
COPY pyproject.toml constraints.txt ./
RUN python -c "import subprocess,tomllib; p=tomllib.load(open('pyproject.toml','rb'))['project']; subprocess.check_call(['pip','install','--no-cache-dir','--constraint','constraints.txt',*p['dependencies'],*p['optional-dependencies']['test']])" && useradd --create-home --uid 10001 checkstock
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests
USER checkstock
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
