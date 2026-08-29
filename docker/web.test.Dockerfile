# 测试环境使用与生产一致的静态构建形态，但独立固定 pnpm 版本，避免 pnpm@latest 漂移。
FROM node:24-alpine AS build-stage

WORKDIR /app

ARG PNPM_VERSION=10.11.0
RUN npm install -g "pnpm@${PNPM_VERSION}"

COPY ./web/package*.json ./
COPY ./web/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

COPY ./web .
RUN pnpm run build

FROM nginx:alpine AS test

COPY --from=build-stage /app/dist /usr/share/nginx/html
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} \; \
    && find /usr/share/nginx/html -type f -exec chmod 644 {} \;

COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./docker/nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
