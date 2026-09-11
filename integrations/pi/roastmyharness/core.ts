import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export const SUBMIT_TOOL = "submit_roast_experiment";
export const AWAIT_TOOL = "await_roast_experiment";
export const DEFAULT_RECENT_TRIALS = 20;
export const WATCH_INTERVAL_SEC = 2;
export const ABORT_GRACE_MS = 3_000;
const MATRIX_MAX_ROWS = 40;

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
		resolved_pi_version?: string;
		thinking?: string;
		repetitions?: number;
		hypothesis?: string;
		control?: string;
		evaluation?: string;
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
	elapsed_sec?: number;
}

export type RoastDetails = RoastResponse | WatchDetails;

export type ExecHost = Pick<ExtensionAPI, "exec">;
export const ROAST_JSON_TIMEOUT_MS = 120_000;

export function roastBinary(): string {
	return process.env.ROAST_MY_HARNESS_BIN || "roastmyharness";
}

export function bridgeArgs(op: "inspect" | "validate" | "run" | "status" | "cancel", target: string): string[] {
	return ["_bridge", op, target];
}

export async function runBridgeJson(host: ExecHost, args: string[], opts?: { signal?: AbortSignal; timeout_ms?: number }): Promise<RoastResponse> {
	let result;
	try {
		result = await host.exec(roastBinary(), args, { signal: opts?.signal, timeout: opts?.timeout_ms ?? ROAST_JSON_TIMEOUT_MS });
	} catch (error) {
		throw new Error(`failed to run ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`);
	}
	const stdout = result.stdout.trim();
	let parsed: RoastResponse | undefined;
	try {
		if (stdout) parsed = JSON.parse(stdout) as RoastResponse;
	} catch {}
	if (!parsed) throw new Error((result.stderr.trim() || stdout || `exit code ${result.code}`).slice(0, 4000));
	if (result.code !== 0 && parsed.error) throw new Error(`error ${parsed.error.code ?? "unknown"}: ${parsed.error.message ?? stdout}`);
	return parsed;
}

export function summarize(r: RoastResponse): string {
	if (r.state === "needs_input") {
		const qs = (r.questions ?? []).map((q) => `  - ${q.field}: ${q.message}`).join("\n");
		return `needs_input:\n${qs}`;
	}
	if (r.state === "ready_for_confirmation") {
		const e = r.experiment;
		return `ready_for_confirmation plan=${r.plan_id}: ${e?.trials ?? "?"} trials ` +
			`(${e?.tasks ?? "?"} tasks x ${e?.arms ?? "?"} arms x ${e?.repetitions ?? 1} reps), ` +
			`max_parallel=${e?.max_parallel ?? "?"}, model=${e?.model ?? "?"}` +
			(e?.resolved_pi_version ? `, pi=${e.resolved_pi_version}` : "") +
			(r.warnings?.length ? `; warnings: ${r.warnings.join("; ")}` : "");
	}
	const parts = [`state=${r.state ?? "unknown"}`];
	if (r.experiment_id) parts.push(`experiment=${r.experiment_id}`);
	if (r.started !== undefined) parts.push(`started=${r.started}`);
	return parts.join(" ");
}

export function formatTokens(count: number): string {
	const k = count / 1000;
	if (k >= 100) return `${Math.round(k)}k`;
	if (k >= 1) return `${k.toFixed(0)}k`;
	return count.toFixed(0);
}

export function formatElapsed(sec: number): string {
	if (!Number.isFinite(sec) || sec < 0) return "0s";
	const total = Math.floor(sec);
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const seconds = total % 60;
	if (hours > 0) return `${hours}h${minutes}m`;
	if (minutes > 0) return `${minutes}m${String(seconds).padStart(2, "0")}s`;
	return `${seconds}s`;
}

export function formatAggregates(aggregates: Record<string, Record<string, number>> | undefined, theme: ThemeLike): string {
	if (!aggregates) return "";
	return Object.entries(aggregates).map(([variant, agg]) =>
		`  ${theme.fg("accent", variant)}: ${agg.resolved ?? 0}/${agg.n ?? 0} resolved`).join("\n");
}

function statusIcon(status: string, theme: ThemeLike): string {
	switch (status) {
		case "P": return theme.fg("success", "P");
		case "F": return theme.fg("error", "F");
		case "E": return theme.fg("warning", "E");
		default: return theme.fg("dim", ".");
	}
}

function truncate(s: string, n: number): string {
	return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function renderMatrix(matrix: Record<string, Record<string, string>>, theme: ThemeLike, maxRows = MATRIX_MAX_ROWS): string {
	const variants = Object.keys(matrix);
	const tasks = [...new Set(variants.flatMap((v) => Object.keys(matrix[v])))];
	if (!variants.length || !tasks.length) return "";
	const colWidth = Math.max(...variants.map((v) => v.length), 8);
	const labelWidth = 24;
	let text = "  ".padEnd(labelWidth + 2);
	text += variants.map((v) => theme.fg("muted", truncate(v, colWidth).padEnd(colWidth))).join("");
	for (const task of tasks.slice(0, maxRows)) {
		const label = truncate(task, labelWidth).padEnd(labelWidth);
		text += `\n  ${theme.fg("dim", label)}` + variants.map((v) => statusIcon(matrix[v][task] ?? ".", theme) + " ".repeat(colWidth - 1)).join("");
	}
	return text;
}

export function countDone(details: WatchDetails): { done: number; total: number } {
	let done = 0, total = 0;
	for (const cells of Object.values(details.matrix ?? {})) {
		for (const status of Object.values(cells)) {
			total += 1;
			if (["P", "F", "E"].includes(status)) done += 1;
		}
	}
	return { done, total };
}

export function oneLineStatus(details: WatchDetails): string {
	const { done, total } = countDone(details);
	return `state=${details.state} done=${done}/${total}` + (details.detached ? " (detached)" : "");
}

export function finalText(details: WatchDetails): string {
	const lines = [`experiment ${details.experiment_id}: ${details.state}`];
	if (details.report?.markdown) lines.push(`report: ${details.report.markdown}`);
	if (details.note) lines.push(`note: ${details.note}`);
	return lines.join("\n");
}
