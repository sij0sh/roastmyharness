# Verifier image: fully self-contained (no registry base). Reproduces the
# agent environment from scratch, then bakes the held-out tests in.
# tests/ is the build context; the agent never sees this container.
FROM public.ecr.aws/x8v8d7g8/mars-base:latest

WORKDIR /app

ARG BASE_SHA=954e5a474b9e3d45ca58f02a3a4eac8e1947acc5
RUN git clone https://github.com/sql-formatter-org/sql-formatter . \
 && DEFAULT="$(git remote show origin | sed -n 's/.*HEAD branch: //p')" \
 && git checkout -B "$DEFAULT" "$BASE_SHA" \
 && git remote remove origin \
 && for b in $(git for-each-ref --format='%(refname:short)' refs/heads | grep -vx "$DEFAULT"); do git branch -D "$b" || true; done \
 && for t in $(git tag); do git merge-base --is-ancestor "$t" HEAD 2>/dev/null || git tag -d "$t"; done \
 && git reflog expire --expire=now --all \
 && git gc --prune=now \
 && (git submodule update --init --recursive || true)

RUN NODE_ENV=development npm install --ignore-scripts && npx nearleyc src/parser/grammar.ne -o src/parser/grammar.ts

RUN mkdir -p /opt/jest-ctrf \
 && cd /opt/jest-ctrf \
 && npm install --no-audit --no-fund jest-ctrf-json-reporter@0.0.11 jest-environment-node@29.7.0 \
 && node -e "require('/opt/jest-ctrf/node_modules/jest-ctrf-json-reporter')" \
 && node -e "require.resolve('/opt/jest-ctrf/node_modules/jest-ctrf-json-reporter/dist/index.js')"

RUN git checkout -- yarn.lock \
 && rm -f package-lock.json \
 && git status --porcelain \
 && test -z "$(git status --porcelain)"

RUN git config --system user.name "roastmyharness" && git config --system user.email "roastmyharness@local" && git config --system --add safe.directory /app

COPY test.sh /tests/test.sh
COPY test.patch /tests/test.patch
COPY grader.py /tests/grader.py
COPY config.json /tests/config.json
COPY phase_scores.py /tests/phase_scores.py
RUN chmod +x /tests/test.sh
