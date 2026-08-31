import type { Usage } from "@earendil-works/pi-ai";

export const SERVICE_ACTIONS = ["prepare", "start", "status", "watch", "cancel", "report"] as const;
export type ServiceAction = (typeof SERVICE_ACTIONS)[number];
export const ROAST_ACTIONS = ["author", ...SERVICE_ACTIONS] as const;
export type RoastAction = (typeof ROAST_ACTIONS)[number];

export const DEFAULT_RECENT_TRIALS = 20;
export const WATCH_INTERVAL_SEC = 2;
export const ABORT_GRACE_MS = 3_000;
const MATRIX_MAX_ROWS = 40;
export const AUTHOR_ACTIVITY_LIMIT = 20;
export const AUTHOR_OUTPUT_LIMIT = 12_000;
export const STDERR_LIMIT = 8_000;
export const AUTHOR_CHILD_ENV = "ROAST_MY_HARNESS_AUTHOR_CHILD";
export const DEFAULT_PI_VERSION = "0.84.3";
export const SUITE_SCREEN_SIZE = 30;

export interface DeepSweSuites {
	root: string;
	suites: Record<string, { label: string; signal: string[]; confirmation: string[] }>;
}

export type ThemeFn = (color: any, text: string) => string;
export interface ThemeLike {
	fg: ThemeFn;
	bold(text: string): string;
}

export interface RoastResponse {
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
		name?: string;
		pi_version?: string;
		thinking?: string;
		control?: string;
		task_ids?: string[];
		tasks_path?: string;
		arm_ids?: string[];
		variant_sources?: Record<string, string[]>;
	};
	warnings?: string[];
	next_action?: string;
	questions?: Array<{ field: string; message: string; choices: string[] }>;
	error?: { code?: string; message?: string };
	[key: string]: unknown;
}

export interface TrialStats {
	input_tokens?: number;
	output_tokens?: number;
	cache_tokens?: number;
	tool_calls?: number;
	turns?: number;
	wall_sec?: number;
}

export interface TrialEvent {
	variant: string;
	task: string;
	status: string;
	reward?: number;
	stats?: TrialStats;
}

export interface WatchDetails {
	stream: true;
	experiment_id: string;
	state: string;
	final: boolean;
	ended?: boolean;
	detached?: boolean;
	note?: string;
	totals?: Record<string, Record<string, number>>;
	matrix?: Record<string, Record<string, string>>;
	running?: [string, string][];
	recent: TrialEvent[];
	summaries: TrialEvent[];
	aggregates?: Record<string, Record<string, number>>;
	report?: { markdown: string; csv: string } | null;
}

export interface AuthorDetails {
	kind: "author";
	phase: "starting" | "authoring" | "validating" | "ready" | "needs_input" | "cancelled";
	final: boolean;
	spec_path?: string;
	attempt: number;
	activities: string[];
	output: string;
	model?: string;
	spec_preview?: string;
	prepared?: RoastResponse;
}

export type RoastDetails = RoastResponse | WatchDetails | AuthorDetails;

export const emptyUsage = (): Usage => ({
	input: 0,
	output: 0,
	cacheRead: 0,
	cacheWrite: 0,
	totalTokens: 0,
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
});

function numeric(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function statNumber(value: unknown): number | undefined {
	if (typeof value === "number" && Number.isFinite(value)) return value;
	if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
		return Number(value);
	}
	return undefined;
}

export function addUsage(target: Usage, value: unknown): void {
	if (!value || typeof value !== "object" || Array.isArray(value)) return;
	const usage = value as Record<string, unknown>;
	const cost = usage.cost && typeof usage.cost === "object" && !Array.isArray(usage.cost)
		? usage.cost as Record<string, unknown>
		: {};
	target.input += numeric(usage.input);
	target.output += numeric(usage.output);
	target.cacheRead += numeric(usage.cacheRead);
	target.cacheWrite += numeric(usage.cacheWrite);
	target.totalTokens += numeric(usage.totalTokens);
	target.cost.input += numeric(cost.input);
	target.cost.output += numeric(cost.output);
	target.cost.cacheRead += numeric(cost.cacheRead);
	target.cost.cacheWrite += numeric(cost.cacheWrite);
	target.cost.total += numeric(cost.total);
	if (usage.reasoning !== undefined) {
		target.reasoning = (target.reasoning ?? 0) + numeric(usage.reasoning);
	}
	if (usage.cacheWrite1h !== undefined) {
		target.cacheWrite1h = (target.cacheWrite1h ?? 0) + numeric(usage.cacheWrite1h);
	}
}

export function roastBinary(): string {
	return process.env.ROAST_MY_HARNESS_BIN || "roastmyharness";
}

export function buildArgs(params: {
	action: ServiceAction;
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

export function summarize(r: RoastResponse): string {
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

export function formatAggregates(
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

export function renderMatrix(
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

export function renderTrials(trials: TrialEvent[], theme: ThemeLike, limit?: number): string {
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

const STATUS_WORDS: Record<string, string> = { P: "pass", F: "fail", E: "error" };

export function renderTrialSummaries(summaries: TrialEvent[], theme: ThemeLike): string {
	const columns = ["in", "out", "cache", "tools", "turns", "wall"] as const;
	const statKeys = [
		"input_tokens",
		"output_tokens",
		"cache_tokens",
		"tool_calls",
		"turns",
		"wall_sec",
	] as const;
	const lines: string[] = [];
	for (const s of summaries) {
		const word = STATUS_WORDS[s.status] ?? s.status;
		const color = s.status === "P"
			? "success"
			: s.status === "F"
				? "error"
				: "warning";
		const reward = s.reward !== undefined ? ` · reward ${s.reward}` : "";
		lines.push(
			`  ${statusIcon(s.status, theme.fg)} ${theme.fg("accent", s.variant)}` +
				theme.fg("dim", "/") +
				`${s.task}` +
				theme.fg(color, ` · ${word}`) +
				theme.fg("dim", reward),
		);
		const cells = statKeys.map((key, i) => {
			const value = s.stats?.[key];
			if (value === undefined) return "-";
			return key === "wall_sec" ? `${value}s` : String(value);
		});
		const widths = cells.map((cell, i) => Math.max(cell.length, columns[i].length));
		const pad = (cell: string, i: number) =>
			i === cells.length - 1 ? cell : cell.padEnd(widths[i]);
		lines.push(
			"    " + columns.map((c, i) => theme.fg("muted", pad(c, i))).join("  "),
		);
		lines.push(
			"    " + cells.map((cell, i) => theme.fg(color, pad(cell, i))).join("  "),
		);
	}
	return lines.join("\n");
}

export function countDone(details: WatchDetails): { done: number; total: number } {
	let done = 0;
	let total = 0;
	for (const cells of Object.values(details.matrix ?? {})) {
		for (const status of Object.values(cells)) {
			total += 1;
			if (["P", "F", "E", "H"].includes(status)) done += 1;
		}
	}
	return { done, total };
}

export function oneLineStatus(details: WatchDetails): string {
	const { done, total } = countDone(details);
	const running = details.running?.length ?? 0;
	return `state=${details.state} done=${done}/${total}` +
		(running ? ` running=${running}` : "") +
		(details.detached ? " (detached)" : "");
}

export function finalText(details: WatchDetails): string {
	const lines = [`experiment ${details.experiment_id}: ${details.state}`];
	const plain: ThemeLike = { fg: (_c, t) => t, bold: (t) => t };
	const agg = formatAggregates(details.aggregates, plain);
	if (agg) lines.push(agg);
	if (details.report?.markdown) lines.push(`report: ${details.report.markdown}`);
	if (details.report?.csv) lines.push(`csv: ${details.report.csv}`);
	if (details.note) lines.push(`note: ${details.note}`);
	return lines.join("\n");
}
