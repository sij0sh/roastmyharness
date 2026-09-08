FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Empty initial commit: the verifier grades /app/record.json and the
# patch collector snapshots the agent's work against HEAD.
RUN git init -q . \
 && git -c user.name="seed" -c user.email="seed@local" commit -q --allow-empty -m "seed" \
 && git config --system user.name "roastmyharness" \
 && git config --system user.email "roastmyharness@local" \
 && git config --system --add safe.directory /app

CMD ["/bin/bash"]
