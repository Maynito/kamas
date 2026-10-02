# Front Kamas — présentation (Jinja + httpx). Contexte de build = racine du repo.
FROM python:3.13-slim
WORKDIR /app
COPY services/front/requirements.txt services/front/requirements.txt
RUN pip install --no-cache-dir -r services/front/requirements.txt
COPY src/web/ src/web/
COPY services/front/ services/front/
EXPOSE 8200
ENV KAMAS_API_URL=http://api:8100
# Les icônes (data/icons) viennent d'un volume monté en lecture seule.
CMD ["uvicorn", "app.main:app", "--app-dir", "services/front", "--host", "0.0.0.0", "--port", "8200"]
