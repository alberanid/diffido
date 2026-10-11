FROM node:22-alpine AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY scripts/build-assets.cjs scripts/
RUN npm run build

FROM python:3.13-alpine
LABEL maintainer="Davide Alberani <da@mimante.net>"
EXPOSE 3210
RUN apk add --no-cache git
WORKDIR /diffido
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY diffido.py ./
COPY dist ./dist/
COPY --from=frontend /build/dist/static/vendor ./dist/static/vendor/
COPY ssl ./ssl/
VOLUME /diffido/conf /diffido/storage
ENTRYPOINT ["./diffido.py"]
