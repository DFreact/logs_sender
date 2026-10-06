FROM postgres:16.15-bookworm@sha256:efedf3595f1d6f415c08568ba171029bf54052e754cc9f030e3f2412b21f3d67
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*
