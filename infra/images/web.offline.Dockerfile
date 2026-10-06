ARG BUILD_DEPENDENCIES=eventhub-web-build-deps:0.12.0
ARG RUNTIME=eventhub-web-runtime:0.12.0
FROM ${BUILD_DEPENDENCIES} AS build
COPY frontend/ /app/
ARG VITE_TIME_ZONE=Europe/Moscow
ENV VITE_TIME_ZONE=${VITE_TIME_ZONE}
RUN npm run build
FROM ${RUNTIME}
USER root
RUN rm -rf /usr/share/nginx/html/*
COPY infra/nginx/nginx.conf /etc/nginx/nginx.conf
COPY infra/nginx/api-errors.conf /etc/nginx/api-errors.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER nginx
