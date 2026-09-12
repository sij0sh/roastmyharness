import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { Text, type Component } from "@earendil-works/pi-tui";
import { cardBox, runCardBg, type CardTheme } from "./cards.ts";
import {
	ABORT_GRACE_MS,
	DEFAULT_RECENT_TRIALS,
	countDone,
	finalText,
	oneLineStatus,
	renderMatrix,
	roastBinary,
	type ThemeLike,
	type WatchDetails,
} from "./core.ts";

export interface WatchParams {
	interval_sec?: number;
	recent?: number;
}

function parseNdjson(line: string): Record<string, unknown> | null {
	try {
		const v = JSON.parse(line) as unknown;
		return typeof v === "object" && v !== null ? (v as Record<string, unknown>) : null;
	} catch {
		return null;
	}
}

/**
 * Spawn a bridge command that streams NDJSON progress (`_bridge run`)
 * and fold its events into live WatchDetails. Resolves on the final
 * event; a closed pipe without a final event resolves as ended.
 */
export async function streamBridgeRun(
	argv: string[],
	initialExperimentId: string,
	signal: AbortSignal | undefined,
	onUpdate?: (text: string, details: WatchDetails) => void,
): Promise<WatchDetails> {
	const details: WatchDetails = { stream: true, experiment_id: initialExperimentId, state: "RUNNING", final: false, recent: [], summaries: [] };
	const startedAt = Date.now();
	let stderrTail = "";
	let aborted = false;
	return new Promise((resolve, reject) => {
		const child = spawn(roastBinary(), argv, { signal });
		const decoder = new StringDecoder("utf8");
		let buffer = "";
		let settled = false;
		const emit = () => {
			details.elapsed_sec = (Date.now() - startedAt) / 1000;
			onUpdate?.(oneLineStatus(details), { ...details, recent: [...details.recent], summaries: [...details.summaries] });
		};
		const finish = (finalDetails: WatchDetails) => {
			if (settled) return;
			settled = true;
			try {
				child.kill();
			} catch {}
			resolve(finalDetails);
		};
		child.stdout.on("data", (chunk: Buffer) => {
			buffer += decoder.write(chunk);
			let idx: number;
			while ((idx = buffer.indexOf("\n")) >= 0) {
				const line = buffer.slice(0, idx).trim();
				buffer = buffer.slice(idx + 1);
				if (!line) continue;
				const evt = parseNdjson(line);
				if (!evt) continue;
				applyEvent(details, evt);
				emit();
				if (details.final) {
					finish(details);
					return;
				}
			}
		});
		child.stderr?.on("data", (chunk: Buffer) => {
			stderrTail += chunk.toString("utf8");
			if (stderrTail.length > 2000) stderrTail = stderrTail.slice(-2000);
		});
		const wasAborted = () => aborted || signal?.aborted === true;
		child.on("error", (error) => {
			if (!settled) {
				if (wasAborted() && (error as NodeJS.ErrnoException)?.code === "ABORT_ERR") return;
				settled = true;
				reject(error);
			}
		});
		child.on("close", (code) => {
			if (!settled) {
				settled = true;
				details.ended = true;
				if (!details.final && !details.note) {
					if (aborted) {
						details.note = "wait aborted before a final event (tool call ended); the run continues detached — call await again to re-attach";
					} else if (code) {
						const tail = stderrTail.trim().replace(/\s+/g, " ").slice(-300);
						details.note = tail
							? `bridge exited ${code} before a final event: ${tail}`
							: `bridge exited ${code} before a final event`;
					} else {
						details.note = "bridge pipe closed before a final event";
					}
				}
				resolve(details);
			}
		});
		if (signal) {
			signal.addEventListener("abort", () => {
				aborted = true;
				setTimeout(() => { try { child.kill("SIGKILL"); } catch {} }, ABORT_GRACE_MS);
				try { child.kill("SIGTERM"); } catch {}
			}, { once: true });
		}
	});
}

function applyEvent(details: WatchDetails, evt: Record<string, unknown>): void {
	if (typeof evt.experiment_id === "string" && evt.experiment_id) {
		details.experiment_id = evt.experiment_id;
	}
	const event = evt.event as string | undefined;
	if (event === "started") return;
	if (evt.ok === false && !details.note) {
		const error = evt.error as { code?: unknown; message?: unknown } | undefined;
		const message = typeof error?.message === "string" && error.message
			? error.message
			: typeof error?.code === "string" && error.code
				? error.code
				: "unknown bridge error";
		details.note = `bridge error before a final event: ${message}`;
		return;
	}
	if (event === "trial") {
		details.recent.push({
			variant: String(evt.variant ?? "?"),
			task: String(evt.task ?? "?"),
			status: String(evt.status ?? "."),
			reward: typeof evt.reward === "number" ? evt.reward : undefined,
		});
		const cap = DEFAULT_RECENT_TRIALS;
		if (details.recent.length > cap) details.recent.splice(0, details.recent.length - cap);
		return;
	}
	if (event === "final") {
		if (typeof evt.state === "string") details.state = evt.state;
		if (evt.matrix && typeof evt.matrix === "object") details.matrix = evt.matrix as WatchDetails["matrix"];
		if (evt.totals && typeof evt.totals === "object") details.totals = evt.totals as WatchDetails["totals"];
		if (evt.aggregates && typeof evt.aggregates === "object") details.aggregates = evt.aggregates as WatchDetails["aggregates"];
		if (evt.report && typeof evt.report === "object") details.report = evt.report as WatchDetails["report"];
		if (typeof evt.run_dir === "string" && evt.run_dir) details.run_dir = evt.run_dir;
		if (evt.charts && typeof evt.charts === "object") details.charts = evt.charts as WatchDetails["charts"];
		if (typeof evt.note === "string") details.note = evt.note;
		details.final = evt.final === true;
		return;
	}
	if ((evt as { matrix?: unknown }).matrix && typeof evt.matrix === "object") {
		details.matrix = evt.matrix as WatchDetails["matrix"];
	}
	if ((evt as { totals?: unknown }).totals && typeof evt.totals === "object") {
		details.totals = evt.totals as WatchDetails["totals"];
	}
	if (typeof evt.state === "string") details.state = evt.state;
	if (evt.final === true) details.final = true;
}

export function renderWatchResult(details: WatchDetails, opts: { expanded: boolean }, theme: ThemeLike): Text {
	const { done, total } = countDone(details);
	let text = `${theme.fg("accent", theme.bold("roastmyharness"))} ${theme.fg("muted", details.experiment_id)} · ${details.state} ${done}/${total}`;
	if (details.matrix) {
		const matrix = renderMatrix(details.matrix, theme);
		if (matrix) text += `\n${matrix}`;
	}
	return new Text(text, 0, 0);
}

export function renderRunCard(details: WatchDetails, expanded: boolean, theme: ThemeLike): Component {
	const { done, total } = countDone(details);
	let text = `${theme.fg("accent", theme.bold("roastmyharness"))} ${theme.fg("muted", details.experiment_id)} · ${details.state} ${done}/${total}`;
	if (details.matrix) {
		const matrix = renderMatrix(details.matrix, theme);
		if (matrix) text += `\n${matrix}`;
	}
	const tail = finalText(details);
	if (tail) text += `\n${theme.fg("dim", tail.split("\n").slice(1).join("\n"))}`;
	return cardBox(theme as CardTheme, runCardBg(details), new Text(text, 0, 0));
}
