FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home app
COPY . .
RUN chown -R app:app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py bootstrap && python manage.py collectstatic --noinput && gunicorn backend.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60"]
