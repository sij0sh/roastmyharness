"""Machine-facing orchestration: one service, thin clients on top.

The experiment service exposes prepare/start/status/cancel/report with
stable JSON shapes so the private bridge CLI and the Pi extension stay
behaviorally identical. Plans are persisted under data_dir()/plans and
bound to the exact bytes they approved.
"""
