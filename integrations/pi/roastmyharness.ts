import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";

const ROAST_ACTIONS = ["prepare", "start", "status", "watch", "cancel", "report"] as const;
type RoastAction = (typeof ROAST_ACTIONS)[number];

const DEFAULT_RECENT_TRIALS = 20;
const WATCH_INTERVAL_SEC = 2;
const ABORT_GRACE_MS = 3_000;
const MATRIX_MAX_ROWS = 40;

type ThemeFn = (color: any, text: string) => string;
interface ThemeLike {
	fg: ThemeFn;
	bold(text: string): string;
}

interface RoastResponse {
	ok?: boolean;
	state?: string;
	plan_id?: string;
	spec_path?: string;
	experiment_id?: string;
	started?: boolean;
	experiment?: {
		tasks: number;
		arms: number;
		trials: number;
		max_parallel: number;
		model: string;
	};
	warnings?: string[];
	next_action?: string;
	questions?: Array<{ field: string; message: string; choices: string[] }>;
	error?: { code?: string; message?: string };
	[key: string]: unknown;
}

interface TrialEvent {
	variant: string;
	task: string;
	status: string;
	reward?: number;
}

interface WatchDetails {
	stream: true;
	experiment_id: string;
	state: string;
	final: boolean;
	detached?: boolean;
	note?: string;
	totals?: Record<string, Record<string, number>>;
	matrix?: Record<string, Record<string, string>>;
	running?: [string, string][];
	recent: TrialEvent[];
	aggregates?: Record<string, Record<string, number>>;
	report?: { markdown: string; csv: string } | null;
}

type RoastDetails = RoastResponse | WatchDetails;

function roastBinary(): string {
	return process.env.ROAST_MY_HARNESS_BIN || "roastmyharness";
}

function buildArgs(params: {
	action: RoastAction;
	spec_path?: string;
	plan_id?: string;
	experiment_id?: string;
	skip_docker?: boolean;
}): string[] {
	const argv = ["tool", params.action];
	switch (params.action) {
		case "prepare":
			argv.push(params.spec_path ?? "");
			break;
		case "start":
			argv.push(params.plan_id ?? "");
			break;
		case "status":
		case "cancel":
		case "report":
			argv.push(params.experiment_id ?? "");
			break;
		case "watch":
			break;
	}
	if (params.skip_docker) argv.push("--skip-docker");
	return argv;
}

function summarize(r: RoastResponse): string {
	if (r.state === "needs_input") {
		const qs = (r.questions ?? [])
			.map((q) => `  - ${q.field}: ${q.message}`)
			.join("\n");
		return `needs_input:\n${qs}`;
	}
	if (r.state === "ready_for_confirmation") {
		const e = r.experiment;
		return `ready_for_confirmation plan=${r.plan_id}: ${e?.trials ?? "?"} trials ` +
			`(${e?.tasks ?? "?"} tasks x ${e?.arms ?? "?"} arms), max_parallel=${e?.max_parallel ?? "?"}, ` +
			`model=${e?.model ?? "?"}` +
			(r.warnings?.length ? `; warnings: ${r.warnings.join("; ")}` : "");
	}
	const parts = [`state=${r.state ?? "unknown"}`];
	if (r.experiment_id) parts.push(`experiment=${r.experiment_id}`);
	if (r.started !== undefined) parts.push(`started=${r.started}`);
	if (r.state === "COMPLETE") parts.push("final=true");
	return parts.join(" ");
}

function formatTokens(count: number): string {
	const k = count / 1000;
	if (k >= 100) return `${Math.round(k)}k`;
	if (k >= 1) return `${k.toFixed(0)}k`;
	return count.toFixed(0);
}

function formatAggregates(
	aggregates: Record<string, Record<string, number>> | undefined,
	theme: ThemeLike,
): string {
	if (!aggregates) return "";
	const lines: string[] = [];
	for (const [variant, agg] of Object.entries(aggregates)) {
		const n = agg.n ?? 0;
		const resolved = agg.resolved ?? 0;
		lines.push(
			`  ${theme.fg("accent", variant)}: ` +
				`${resolved}/${n} resolved · ` +
				`in ${formatTokens(agg.input_tokens ?? 0)} · ` +
				`out ${formatTokens(agg.output_tokens ?? 0)} · ` +
				`wall ${Math.round((agg.wall_sec ?? 0) / 60)}m · ` +
				`$${(agg.cost_usd ?? 0).toFixed(2)}`,
		);
	}
	return lines.join("\n");
}

function statusIcon(status: string, fg: ThemeFn): string {
	switch (status) {
		case "P":
			return fg("success", "P");
		case "F":
			return fg("error", "F");
		case "E":
			return fg("warning", "E");
		case "~":
			return fg("accent", "~");
		case "H":
			return fg("muted", "H");
		default:
			return fg("dim", ".");
	}
}

function truncate(s: string, n: number): string {
	return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function renderMatrix(
	matrix: Record<string, Record<string, string>>,
	theme: ThemeLike,
	maxRows = MATRIX_MAX_ROWS,
): string {
	const variants = Object.keys(matrix);
	const tasks = [...new Set(variants.flatMap((v) => Object.keys(matrix[v])))];
	if (!variants.length || !tasks.length) return "";
	const colWidth = Math.max(...variants.map((v) => v.length), 8);
	const labelWidth = 24;
	let text = "  ".padEnd(labelWidth + 2);
	text += variants
		.map((v) => theme.fg("muted", truncate(v, colWidth).padEnd(colWidth)))
		.join("");
	const shown = tasks.slice(0, maxRows);
	for (const task of shown) {
		const label = truncate(task, labelWidth).padEnd(labelWidth);
		const cells = variants
			.map((v) => {
				const symbol = matrix[v][task] ?? ".";
				return statusIcon(symbol, theme.fg) + " ".repeat(colWidth - 1);
			});
		text += `\n  ${theme.fg("dim", label)}${cells.join("")}`;
	}
	if (tasks.length > shown.length) {
		text += `\n${theme.fg("muted", `  ... +${tasks.length - shown.length} more tasks`)}`;
	}
	return text;
}

function renderTrials(trials: TrialEvent[], theme: ThemeLike, limit?: number): string {
	const shown = limit ? trials.slice(-limit) : trials;
	return shown
		.map((t) => {
			const icon = statusIcon(t.status, theme.fg);
			const reward =
				t.reward !== undefined ? theme.fg("dim", ` reward=${t.reward}`) : "";
			return `  ${icon} ${theme.fg("accent", t.variant)}/${t.task}${reward}`;
		})
		.join("\n");
}

function countDone(details: WatchDetails): { done: number; total: number } {
	let done = 0;
	let total = 0;
	for (const counts of Object.values(details.totals ?? {})) {
		done += (counts.P ?? 0) + (counts.F ?? 0) + (counts.E ?? 0);
	}
	for (const cells of Object.values(details.matrix ?? {})) {
		total += Object.keys(cells).length;
	}
	return { done, total };
}

function oneLineStatus(details: WatchDetails): string {
	const { done, total } = countDone(details);
	const running = details.running?.length ?? 0;
	return `state=${details.state} done=${done}/${total}` +
		(running ? ` running=${running}` : "") +
		(details.detached ? " (detached)" : "");
}

function finalText(details: WatchDetails): string {
	const lines = [`experiment ${details.experiment_id}: ${details.state}`];
	const plain: ThemeLike = { fg: (_c, t) => t, bold: (t) => t };
	const agg = formatAggregates(details.aggregates, plain);
	if (agg) lines.push(agg);
	if (details.report?.markdown) lines.push(`report: ${details.report.markdown}`);
	if (details.report?.csv) lines.push(`csv: ${details.report.csv}`);
	if (details.note) lines.push(`note: ${details.note}`);
	return lines.join("\n");
}

interface WatchParams {
	interval_sec?: number;
	recent?: number;
}

async function streamWatch(
	experimentId: string,
	params: WatchParams,
	signal: AbortSignal | undefined,
	onUpdate?: OnUpdate,
): Promise<{ content: Array<{ type: "text"; text: string }>; details?: WatchDetails; isError?: boolean }> {
	const recentCap = Math.max(1, Math.min(Math.trunc(params.recent ?? DEFAULT_RECENT_TRIALS), 200));
	const details: WatchDetails = {
		stream: true,
		experiment_id: experimentId,
		state: "?",
		final: false,
		recent: [],
	};

	const emit = () => {
		onUpdate?.({
			content: [{ type: "text", text: oneLineStatus(details) }],
			details: { ...details, recent: [...details.recent] },
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
			details.recent.push({
				variant: String(event.variant ?? "?"),
				task: String(event.task ?? "?"),
				status: String(event.status ?? "?"),
				reward: typeof event.reward === "number" ? event.reward : undefined,
			});
			if (details.recent.length > recentCap) {
				details.recent.splice(0, details.recent.length - recentCap);
			}
		} else if (kind === "final") {
			details.state = String(event.state ?? details.state);
			details.final = Boolean(event.final);
			details.aggregates = (event.aggregates ?? details.aggregates) as WatchDetails["aggregates"];
			details.report = (event.report ?? details.report) as WatchDetails["report"];
			details.note = event.note !== undefined ? String(event.note) : details.note;
			if (event.totals) details.totals = event.totals as WatchDetails["totals"];
			if (event.matrix) details.matrix = event.matrix as WatchDetails["matrix"];
		}
	};

	const argv = [
		"tool",
		"watch",
		experimentId,
		...(params.interval_sec !== undefined
			? ["--interval", String(Math.max(params.interval_sec, 0.2))]
			: []),
	];

	return await new Promise((resolve) => {
		let settled = false;
		let child: ReturnType<typeof spawn>;
		try {
			child = spawn(roastBinary(), argv, { stdio: ["ignore", "pipe", "pipe"] });
		} catch (error) {
			resolve({
				content: [{
					type: "text",
					text: `failed to spawn ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`,
				}],
				isError: true,
			});
			return;
		}
		const exited = () => child.exitCode !== null || child.signalCode !== null;

		let stderr = "";
		let stdoutBuf = "";
		let sawFinal = false;

		child.stdout?.on("data", (chunk: Buffer) => {
			stdoutBuf += chunk.toString();
			const lines = stdoutBuf.split("\n");
			stdoutBuf = lines.pop() ?? "";
			let changed = false;
			for (const line of lines) {
				if (!line.trim()) continue;
				try {
					const event = JSON.parse(line) as Record<string, unknown>;
					applyEvent(event);
					if (event.event === "final") sawFinal = true;
					changed = true;
				} catch {
					
				}
			}
			if (changed) emit();
		});
		child.stderr?.on("data", (chunk: Buffer) => {
			stderr += chunk.toString();
		});

		const finish = (detached: boolean) => {
			if (settled) return;
			settled = true;
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
			resolve({
				content: [{ type: "text", text: errText }],
				details: { ...details },
				isError: details.state === "?" || !details.state,
			});
		};

		child.on("close", () => finish(false));
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
			setTimeout(() => {
				if (!settled && child.exitCode === null && child.signalCode === null) {
					child.kill("SIGKILL");
				}
			}, ABORT_GRACE_MS);
		};
		if (signal?.aborted) onAbort();
		else signal?.addEventListener("abort", onAbort, { once: true });
	});
}

type OnUpdate = (partial: {
	content: Array<{ type: "text"; text: string }>;
	details?: WatchDetails;
}) => void;

function renderWatchResult(
	details: WatchDetails,
	{ expanded }: { expanded: boolean },
	theme: ThemeLike,
): Text {
	const isRunning = !details.final && !details.detached;
	const icon = details.detached
		? theme.fg("warning", "○")
		: details.state === "COMPLETE"
			? theme.fg("success", "✓")
			: details.state === "FAILED" || details.state === "CANCELLED"
				? theme.fg("error", "✗")
				: isRunning
					? theme.fg("warning", "⏳")
					: theme.fg("muted", "○");
	const { done, total } = countDone(details);
	let text = `${icon} ${theme.fg("toolTitle", theme.bold("roastmyharness "))}` +
		theme.fg("accent", details.experiment_id) +
		theme.fg("muted", ` · ${details.state}`) +
		(total ? theme.fg("dim", ` · ${done}/${total} done`) : "");

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
	if (details.final && !expanded) {
		text += `\n${theme.fg("muted", "(Ctrl+O to expand)")}`;
	}
	return new Text(text, 0, 0);
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "roast_harness",
		label: "RoastMyHarness",
		description:
			"Run harness-comparison experiments via the roastmyharness service. " +
			"prepare validates an experiment TOML and returns a plan for user approval; " +
			"start launches an approved plan_id and streams live progress until the " +
			"experiment finishes (watch=false returns immediately after launch); watch " +
			"attaches to a running experiment and streams live progress; status polls " +
			"once; cancel requests graceful cancellation; report regenerates artifacts.",
		promptSnippet:
			"Prepare, launch, monitor, and report harness-comparison experiments via the roastmyharness service",
		promptGuidelines: [
			"Use roast_harness instead of shell commands when comparing harness configurations: prepare, then ask the user to approve the plan, then start with the returned plan_id.",
			"start streams live progress until the experiment finishes; aborting the call only detaches the watch (the run keeps going). Use cancel to actually stop a run.",
			"Read roast_harness results as JSON; when prepare returns needs_input, resolve the listed questions instead of guessing.",
		],
		parameters: Type.Object({
			action: StringEnum(ROAST_ACTIONS, {
				description: "Orchestration action to perform.",
			}),
			spec_path: Type.Optional(
				Type.String({ description: "Experiment TOML path (required for prepare)." }),
			),
			plan_id: Type.Optional(
				Type.String({ description: "Plan id from prepare (required for start)." }),
			),
			experiment_id: Type.Optional(
				Type.String({
					description: "Experiment id (required for status, watch, cancel, report).",
				}),
			),
			watch: Type.Optional(
				Type.Boolean({
					description:
						"After start: stream live progress until final. Default true; set false to return immediately after launch.",
				}),
			),
			interval_sec: Type.Optional(
				Type.Number({
					description: `Watch poll interval in seconds (default ${WATCH_INTERVAL_SEC}).`,
				}),
			),
			recent: Type.Optional(
				Type.Number({
					description: `Trial events kept for display (default ${DEFAULT_RECENT_TRIALS}).`,
				}),
			),
			skip_docker: Type.Optional(
				Type.Boolean({ description: "Skip docker preflight checks." }),
			),
		}),
		executionMode: "sequential",
		async execute(toolCallId, params, signal, onUpdate, ctx) {
			void toolCallId;

			if (params.action === "prepare" && !params.spec_path) {
				return { content: [{ type: "text", text: "spec_path is required for prepare" }], isError: true };
			}
			if (params.action === "start" && !params.plan_id) {
				return { content: [{ type: "text", text: "plan_id is required for start" }], isError: true };
			}
			if (["status", "watch", "cancel", "report"].includes(params.action) && !params.experiment_id) {
				return {
					content: [{ type: "text", text: `experiment_id is required for ${params.action}` }],
					isError: true,
				};
			}

			if (params.action === "start" && ctx.hasUI) {
				const ok = await ctx.ui.confirm(
					"RoastMyHarness",
					`Launch experiment plan ${params.plan_id}?`,
				);
				if (!ok) {
					return {
						content: [{ type: "text", text: "launch cancelled by user" }],
					};
				}
			}

			const wantsWatch =
				params.action === "watch" ||
				(params.action === "start" && params.watch !== false);
			const watchParams: WatchParams = {
				interval_sec: params.interval_sec,
				recent: params.recent,
			};

			if (params.action === "watch") {
				return await streamWatch(params.experiment_id as string, watchParams, signal, onUpdate);
			}

			const argv = buildArgs(params);
			onUpdate?.({
				content: [{ type: "text", text: `running: ${roastBinary()} ${argv.join(" ")}` }],
			});
			let result;
			try {
				result = await pi.exec(roastBinary(), argv, { signal, timeout: 120_000 });
			} catch (error) {
				return {
					content: [
						{
							type: "text",
							text: `failed to run ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`,
						},
					],
					isError: true,
				};
			}

			const stdout = result.stdout.trim();
			let parsed: RoastResponse | null = null;
			if (stdout) {
				try {
					parsed = JSON.parse(stdout) as RoastResponse;
				} catch {
					
				}
			}
			if (result.code !== 0 && parsed?.error) {
				const err = parsed.error;
				return {
					content: [{ type: "text", text: `error ${err.code ?? "unknown"}: ${err.message ?? stdout}` }],
					isError: true,
				};
			}
			if (result.code !== 0 && !parsed) {
				const text = (result.stderr.trim() || stdout || `exit code ${result.code}`).slice(0, 4000);
				return { content: [{ type: "text", text }], isError: true };
			}

			if (wantsWatch && parsed?.experiment_id) {
				return await streamWatch(parsed.experiment_id, watchParams, signal, onUpdate);
			}

			const text = parsed
				? summarize(parsed)
				: (stdout || "ok").slice(0, 4000);
			return {
				content: [{ type: "text", text }],
				details: parsed ?? {},
			};
		},

		renderCall(args, theme, _context) {
			let text = theme.fg("toolTitle", theme.bold("roast_harness ")) +
				theme.fg("accent", args.action ?? "?");
			if (args.spec_path) text += theme.fg("dim", ` ${args.spec_path}`);
			if (args.plan_id) text += theme.fg("dim", ` ${args.plan_id}`);
			if (args.experiment_id) text += theme.fg("dim", ` ${args.experiment_id}`);
			if (args.action === "start" && args.watch === false) {
				text += theme.fg("muted", " (no watch)");
			}
			return new Text(text, 0, 0);
		},

		renderResult(result, { expanded }, theme, _context) {
			const details = result.details as RoastDetails | undefined;
			if (!details || (details as WatchDetails).stream !== true) {
				const part = result.content.find((c) => c.type === "text");
				return new Text(part && "text" in part ? part.text : "(no output)", 0, 0);
			}
			return renderWatchResult(details, { expanded }, theme);
		},
	});
}
