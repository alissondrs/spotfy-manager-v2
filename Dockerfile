# Dockerfile único para todos os serviços.
# Build context: raiz do projeto. Imagem seleciona o serviço via ARG.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim

ARG SERVICE=web
ARG SERVICE_PORT=8000

ENV SERVICE_PORT=${SERVICE_PORT} \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv/app

WORKDIR /srv

# Contratos compartilhados (editable; dependências: pydantic, PyJWT, python-dotenv)
COPY contracts ./contracts
RUN pip install --no-cache-dir ./contracts

# Dependências do serviço
COPY services/${SERVICE}/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r ./requirements.txt

# Código do serviço
COPY services/${SERVICE} ./app

WORKDIR /srv/app
EXPOSE ${SERVICE_PORT}

CMD uvicorn main:app --host 0.0.0.0 --port ${SERVICE_PORT}