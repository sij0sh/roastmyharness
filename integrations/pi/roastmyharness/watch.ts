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
	type TrialEvent,
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

export async function streamBridgeRun(
	experimentId: string,
	params: WatchParams,
	signal: AbortSignal | undefined,
	onUpdate?: (text: string, details: WatchDetails) => void,
): Promise<WatchDetails> {
	const details: WatchDetails = { stream: true, experiment_id: experimentId, state: "RUNNING", final: false, recent: [], summaries: [] };
	const startedAt = Date.now();
	return new Promise((resolve, reject) => {
		const child = spawn(roastBinary(), ["_bridge", "status", experimentId], { signal });
		const decoder = new StringDecoder("utf8");
		let buffer = "";
		const emit = () => {
			details.elapsed_sec = (Date.now() - startedAt) / 1000;
			onUpdate?.(oneLineStatus(details), { ...details, recent: [...details.recent], summaries: [...details.summaries] });
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
					child.kill();
					resolve(details);
					return;
				}
			}
		});
		child.on("error", reject);
		child.on("close", () => resolve(details));
		if (signal) {
			signal.addEventListener("abort", () => {
				setTimeout(() => { try { child.kill("SIGKILL"); } catch {} }, ABORT_GRACE_MS);
				try { child.kill("SIGTERM"); } catch {}
			}, { once: true });
		}
	});
}

function applyEvent(details: WatchDetails, evt: Record<string, unknown>): void {
	const event = evt.event as string | undefined;
	if (event === "trial") {
		details.recent.push({
			variant: String(evt.variant ?? "?"),
			task: String(evt.task ?? "?"),
			status: String(evt.status ?? "."),
			reward: typeof evt.reward === "number" ? evt.reward : undefined,
		});
		const cap = DEFAULT_RECENT_TRIALS;
		if (details.recent.length > cap) details.recent.splice(0, details.recent.length - cap);
	}
	if ((evt as { matrix?: unknown }).matrix && typeof evt.matrix === "object") {
		details.matrix = evt.matrix as WatchDetails["matrix"];
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
