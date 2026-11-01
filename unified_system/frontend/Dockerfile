ARG NODE_BASE=node:20-alpine
ARG NGINX_BASE=nginx:alpine
FROM ${NODE_BASE} AS build

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
WORKDIR /workspace/frontend
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

FROM ${NGINX_BASE} AS runtime

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /workspace/frontend/dist /usr/share/nginx/html
RUN mkdir -p /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp \
    && chown -R 101:101 /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp \
    && chown -R 101:101 /usr/share/nginx/html
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=4s --start-period=10s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
