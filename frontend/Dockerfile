FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# The API client removes the trailing slash, giving a same-origin /api URL.
ENV VITE_API_BASE_URL=/
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine
USER root
RUN rm /etc/nginx/conf.d/default.conf && chown nginx:nginx /etc/nginx/conf.d
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/ /usr/share/nginx/html/
ENV BACKEND_ORIGIN=http://backend:8001 NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 NGINX_ENVSUBST_FILTER="^(BACKEND_ORIGIN|NGINX_LOCAL_RESOLVERS)$"
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
