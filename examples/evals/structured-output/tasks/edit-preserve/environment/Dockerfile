FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Seed file plus initial commit: the verifier diffs preservation against
# this exact content, and the patch collector snapshots against HEAD.
RUN git init -q . \
 && cat > notes.txt <<'SEED'
# Project notes

owner: ada
status: draft
budget: 1200

## Reminders
- water the plants
- rotate the logs
SEED
 git add -A \
 && git -c user.name="seed" -c user.email="seed@local" commit -qm "seed" \
 && git config --system user.name "roastmyharness" \
 && git config --system user.email "roastmyharness@local" \
 && git config --system --add safe.directory /app

CMD ["/bin/bash"]
