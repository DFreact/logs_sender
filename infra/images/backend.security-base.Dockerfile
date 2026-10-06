FROM eventhub-backend:0.11.0
USER root
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*
USER 10001:10001
