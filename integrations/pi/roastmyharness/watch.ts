import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import type { AgentToolResult, AgentToolUpdateCallback } from "@earendil-works/pi-coding-agent";
import { keyHint } from "@earendil-works/pi-coding-agent";
import { Text, type Component } from "@earendil-works/pi-tui";
import { cardBox, runCardBg, type CardTheme } from "./cards.ts";
import {
	ABORT_GRACE_MS,
	DEFAULT_RECENT_TRIALS,
	STDERR_LIMIT,
	buildArgs,
	countDone,
	finalText,
	formatAggregates,
	formatElapsed,
	formatTokens,
	oneLineStatus,
	renderMatrix,
	renderTrials,
	renderTrialSummaries,
	roastBinary,
	runRoastJson,
	statNumber,
	type ExecHost,
	type RoastResponse,
	type ThemeLike,
	type TrialEvent,
	type TrialStats,
	type WatchDetails,
} from "./core.ts";

export interface WatchParams {
	interval_sec?: number;
	recent?: number;
}

/**
 * Single execution place for launching an approved plan into a detached
 * worker. Both the roast_harness tool (foreground stream) and the
 * /roastmyharness command (background widget stream) start here, so the
 * TOML A/B run has one launch path; callers differ only in how they watch.
 */
export async function startExperiment(
	host: ExecHost,
	planId: string,
	opts?: { skip_docker?: boolean; signal?: AbortSignal },
): Promise<RoastResponse> {
	return runRoastJson(host, buildArgs({ action: "start", plan_id: planId, skip_docker: opts?.skip_docker }), {
		signal: opts?.signal,
	});
}

export async function streamWatch(
	experimentId: string,
	params: WatchParams,
	signal: AbortSignal | undefined,
	onUpdate?: OnUpdate,
): Promise<AgentToolResult<WatchDetails>> {
	const recentCap = Math.max(1, Math.min(Math.trunc(params.recent ?? DEFAULT_RECENT_TRIALS), 200));
	const details: WatchDetails = {
		stream: true,
		experiment_id: experimentId,
		state: "?",
		final: false,
		recent: [],
		summaries: [],
	};
	const startedAt = Date.now();

	const emit = () => {
		details.elapsed_sec = (Date.now() - startedAt) / 1000;
		onUpdate?.({
			content: [{ type: "text", text: oneLineStatus(details) }],
			details: {
				...details,
				recent: [...details.recent],
				summaries: [...details.summaries],
			},
		});
	};

	const applyEvent = (event: Record<string, unknown>) => {
		const kind = event.event;
		if (kind === "snapshot" || kind === "heartbeat") {
			details.state = String(event.state ?? details.state);
			details.totals = (event.totals ?? details.totals) as WatchDetails["totals"];
			details.matrix = (event.matrix ?? details.matrix) as WatchDetails["matrix"];
			details.running = (event.running ?? details.running) as WatchDetails["running"];
		} else if (kind === "state") {
			details.state = String(event.state ?? details.state);
		} else if (kind === "trial") {
			const summary: TrialEvent = {
				variant: String(event.variant ?? "?"),
				task: String(event.task ?? "?"),
				status: String(event.status ?? "?"),
				reward: typeof event.reward === "number" ? event.reward : undefined,
			};
			if (event.stats && typeof event.stats === "object" && !Array.isArray(event.stats)) {
				const raw = event.stats as Record<string, unknown>;
				const stats: TrialStats = {};
				for (const key of [
					"input_tokens",
					"output_tokens",
					"cache_tokens",
					"tool_calls",
					"turns",
					"wall_sec",
				] as const) {
					const value = statNumber(raw[key]);
					if (value !== undefined) stats[key] = value;
				}
				if (Object.keys(stats).length) summary.stats = stats;
			}
			details.summaries.push(summary);
			details.recent.push({
				variant: summary.variant,
				task: summary.task,
				status: summary.status,
				reward: summary.reward,
			});
			if (details.recent.length > recentCap) {
				details.recent.splice(0, details.recent.length - recentCap);
			}
		} else if (kind === "final") {
			details.state = String(event.state ?? details.state);
			details.final = Boolean(event.final);
			details.ended = true;
			details.aggregates = (event.aggregates ?? details.aggregates) as WatchDetails["aggregates"];
			details.report = (event.report ?? details.report) as WatchDetails["report"];
			details.note = event.note !== undefined ? String(event.note) : details.note;
			if (event.totals) details.totals = event.totals as WatchDetails["totals"];
			if (event.matrix) details.matrix = event.matrix as WatchDetails["matrix"];
		}
	};

	const argv = buildArgs({ action: "watch", experiment_id: experimentId, interval_sec: params.interval_sec });

	return await new Promise((resolve, reject) => {
		let settled = false;
		let child: ReturnType<typeof spawn>;
		try {
			child = spawn(roastBinary(), argv, { stdio: ["ignore", "pipe", "pipe"] });
		} catch (error) {
			reject(new Error(
				`failed to spawn ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`,
			));
			return;
		}
		const exited = () => child.exitCode !== null || child.signalCode !== null;

		let stderr = "";
		let stdoutBuf = "";
		const decoder = new StringDecoder("utf8");
		let sawFinal = false;
		let forceTimer: ReturnType<typeof setTimeout> | undefined;
		const consumeLine = (line: string): boolean => {
			if (!line.trim()) return false;
			try {
				const event = JSON.parse(line) as Record<string, unknown>;
				if (event.error && typeof event.error === "object") {
					const error = event.error as Record<string, unknown>;
					stderr = `${String(error.code ?? "watch_error")}: ${String(error.message ?? "watch failed")}`;
					return false;
				}
				applyEvent(event);
				if (event.event === "final") sawFinal = true;
				return true;
			} catch {
				stderr = `${stderr}\ninvalid watch event: ${line}`.slice(-STDERR_LIMIT);
				return false;
			}
		};

		child.stdout?.on("data", (chunk: Buffer) => {
			stdoutBuf += decoder.write(chunk);
			const lines = stdoutBuf.split("\n");
			stdoutBuf = lines.pop() ?? "";
			let changed = false;
			for (const line of lines) changed = consumeLine(line) || changed;
			if (changed) emit();
		});
		child.stderr?.on("data", (chunk: Buffer) => {
			stderr = `${stderr}${chunk.toString()}`.slice(-STDERR_LIMIT);
		});

		const finish = (detached: boolean) => {
			if (settled) return;
			settled = true;
			if (forceTimer) clearTimeout(forceTimer);
			signal?.removeEventListener("abort", onAbort);
			details.detached = detached || undefined;
			if (sawFinal) {
				resolve({ content: [{ type: "text", text: finalText(details) }], details: { ...details } });
				return;
			}
			if (detached) {
				resolve({
					content: [{
						type: "text",
						text: `detached from watch. ${oneLineStatus(details)}. ` +
							`Use action "watch" to re-attach or "cancel" to stop ${details.experiment_id}.`,
					}],
					details: { ...details },
				});
				return;
			}
			const errText = (
				stderr.trim() || `watch exited without a final event (exit code ${child.exitCode})`
			).slice(0, 4000);
			if (details.state === "?" || !details.state) {
				reject(new Error(errText));
				return;
			}
			details.ended = true;
			details.note = errText;
			resolve({
				content: [{ type: "text", text: errText }],
				details: { ...details },
			});
		};

		child.on("close", () => {
			stdoutBuf += decoder.end();
			if (stdoutBuf.trim() && consumeLine(stdoutBuf)) emit();
			finish(false);
		});
		child.on("error", (error) => {
			stderr += `${error}`;
			finish(false);
		});

		const onAbort = () => {
			if (settled) return;
			if (exited()) {
				finish(true);
				return;
			}
			child.removeAllListeners("close");
			child.on("close", () => finish(true));
			child.kill("SIGTERM");
			forceTimer = setTimeout(() => {
				if (!settled && child.exitCode === null && child.signalCode === null) {
					child.kill("SIGKILL");
				}
			}, ABORT_GRACE_MS);
		};
		if (signal?.aborted) onAbort();
		else signal?.addEventListener("abort", onAbort, { once: true });
	});
}

export async function streamStartedExperiment(
	experimentId: string,
	params: WatchParams,
	signal: AbortSignal | undefined,
	onUpdate?: OnUpdate,
): Promise<AgentToolResult<WatchDetails>> {
	const deadline = Date.now() + 12_000;
	while (true) {
		try {
			return await streamWatch(experimentId, params, signal, onUpdate);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			if (!/unknown[_ ]experiment/i.test(message) || Date.now() >= deadline || signal?.aborted) {
				throw error;
			}
			await new Promise<void>((resolveDelay, reject) => {
				const abort = () => {
					clearTimeout(timer);
					reject(new Error("watch cancelled"));
				};
				const timer = setTimeout(() => {
					signal?.removeEventListener("abort", abort);
					resolveDelay();
				}, 250);
				if (signal?.aborted) abort();
				else signal?.addEventListener("abort", abort, { once: true });
			});
		}
	}
}

type OnUpdate = AgentToolUpdateCallback<WatchDetails>;

/** Tokens measured across completed trials plus the completion rate. */
export function trialTelemetry(details: WatchDetails): string {
	let input = 0;
	let output = 0;
	for (const summary of details.summaries) {
		const inTokens = summary.stats?.input_tokens;
		const outTokens = summary.stats?.output_tokens;
		if (typeof inTokens === "number" && Number.isFinite(inTokens)) input += inTokens;
		if (typeof outTokens === "number" && Number.isFinite(outTokens)) output += outTokens;
	}
	const parts: string[] = [];
	if (input > 0 || output > 0) {
		parts.push(`tokens in ${formatTokens(input)} out ${formatTokens(output)}`);
	}
	const elapsed = details.elapsed_sec ?? 0;
	if (elapsed >= 5 && details.summaries.length > 0) {
		parts.push(`${(details.summaries.length / (elapsed / 60)).toFixed(1)} trials/min`);
	}
	return parts.join(" · ");
}

/**
 * Transcript-card entry point for posted run cards: same rendering as the
 * roast_harness watch card, driven by the persisted card payload, in the
 * same colored container native tool cards use.
 */
export function renderRunCard(details: unknown, expanded: boolean, theme: CardTheme): Component {
	if (!details || typeof details !== "object" || (details as WatchDetails).stream !== true) {
		return new Text("(no run data)", 0, 0);
	}
	const typed = details as WatchDetails;
	return cardBox(theme, runCardBg(typed), renderWatchResult(typed, { expanded }, theme));
}

export function renderWatchResult(
	details: WatchDetails,
	{ expanded }: { expanded: boolean },
	theme: ThemeLike,
): Text {
	const isRunning = !details.final && !details.ended && !details.detached;
	const icon = details.detached || (details.ended && !details.final)
		? theme.fg("warning", "○")
		: details.state === "COMPLETE"
			? theme.fg("success", "✓")
			: details.state === "FAILED" || details.state === "CANCELLED"
				? theme.fg("error", "✗")
				: isRunning
					? theme.fg("warning", "⏳")
					: theme.fg("muted", "○");
	const { done, total } = countDone(details);
	const runningCount = details.running?.length ?? 0;
	let text = `${icon} ${theme.fg("toolTitle", theme.bold("Benchmark "))}` +
		theme.fg("accent", details.experiment_id) +
		theme.fg("muted", ` · ${details.state}`) +
		(total ? theme.fg("dim", ` · ${done}/${total} done`) : "") +
		(runningCount ? theme.fg("accent", ` · ${runningCount} running`) : "") +
		(details.elapsed_sec !== undefined ? theme.fg("dim", ` · ${formatElapsed(details.elapsed_sec)}`) : "");
	if (total) {
		const width = 20;
		const filled = Math.min(width, Math.round((done / total) * width));
		text += `\n  ${theme.fg("success", "#".repeat(filled))}` +
			theme.fg("dim", "-".repeat(width - filled)) +
			theme.fg("muted", ` ${Math.round((done / total) * 100)}%`);
	}
	const telemetry = trialTelemetry(details);
	if (telemetry) text += `\n  ${theme.fg("dim", telemetry)}`;

	for (const [variant, counts] of Object.entries(details.totals ?? {})) {
		text += `\n  ${theme.fg("accent", variant)}: ` +
			theme.fg("success", `P ${counts.P ?? 0}`) + " " +
			theme.fg("error", `F ${counts.F ?? 0}`) + " " +
			theme.fg("warning", `E ${counts.E ?? 0}`);
	}

	if (details.running?.length) {
		const shown = details.running.slice(0, expanded ? 12 : 4);
		const rest = details.running.length - shown.length;
		text += `\n${theme.fg("muted", "  running: ")}` +
			shown.map(([v, t]) => `${v}/${t}`).join(", ") +
			(rest > 0 ? theme.fg("muted", ` +${rest} more`) : "");
	}

	if (details.recent.length) {
		const trialBlock = renderTrials(details.recent, theme, expanded ? undefined : 5);
		if (trialBlock) text += `\n${trialBlock}`;
	}

	if (details.summaries.length) {
		const block = renderTrialSummaries(details.summaries, theme);
		if (block) text += `\n${block}`;
	}

	if (expanded && details.matrix && Object.keys(details.matrix).length) {
		const matrix = renderMatrix(details.matrix, theme);
		if (matrix) text += `\n${matrix}`;
	}

	const aggregates = formatAggregates(details.aggregates, theme);
	if (aggregates) text += `\n${aggregates}`;
	if (details.report?.markdown) {
		text += `\n${theme.fg("success", `report: ${details.report.markdown}`)}`;
	}
	if (details.note) text += `\n${theme.fg("warning", details.note)}`;
	if ((details.final || details.ended) && !expanded) {
		text += `\n${theme.fg("muted", keyHint("app.tools.expand", "to expand"))}`;
	}
	return new Text(text, 0, 0);
}
