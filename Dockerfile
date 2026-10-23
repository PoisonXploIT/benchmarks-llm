FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# En el deploy no hay vault ni GPU: solo lectura del snapshot. La api_key del juez
# nube no se usa aqui (el orquestador esta desactivado en modo lectura).
ENV BM_READONLY=1

EXPOSE 8501

CMD ["sh", "-c", "python -m streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
