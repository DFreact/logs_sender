FROM eventhub-web:0.11.0
USER root
RUN apk upgrade --no-cache
USER nginx
