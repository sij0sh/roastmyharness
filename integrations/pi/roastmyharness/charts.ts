import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { Container, Image, Text, type Component } from "@earendil-works/pi-tui";
import { CHART_FILES, type WatchDetails } from "./core.ts";

export const CHARTS_CARD_TYPE = "roastmyharness-charts";

export interface ChartImage {
	name: string;
	base64: string;
}

export interface ChartsDetails {
	runDir: string;
	reportPath: string;
	images: ChartImage[];
	summary: string[];
}

function sortedEntries<T>(record: Record<string, T> | undefined): [string, T][] {
	return Object.entries(record ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

export function chartsRunDir(details: WatchDetails): string | null {
	if (details.charts?.run_dir) return details.charts.run_dir;
	if (details.run_dir) return details.run_dir;
	if (details.report?.markdown) return dirname(details.report.markdown);
	return null;
}

export async function loadChartsDetails(runDir: string, reportPath: string): Promise<ChartsDetails> {
	const images: ChartImage[] = [];
	for (const name of CHART_FILES) {
		try {
			const base64 = await readFile(join(runDir, "charts", name), "base64");
			if (base64) images.push({ name, base64 });
		} catch {}
	}
	const summary: string[] = [];
	try {
		const summaryJson = JSON.parse(await readFile(join(runDir, "summary.json"), "utf8")) as {
			charts?: { arms?: Record<string, { resolved?: number; total?: number; near_miss?: number; mean_partial?: number | null }> };
		};
		for (const [variant, arm] of sortedEntries(summaryJson.charts?.arms)) {
			const partial = typeof arm.mean_partial === "number" ? `, mean partial ${(100 * arm.mean_partial).toFixed(1)}%` : "";
			summary.push(`${variant}: ${arm.resolved ?? 0}/${arm.total ?? 0} resolved, ${arm.near_miss ?? 0} near misses${partial}`);
		}
	} catch {}
	return { runDir, reportPath, images, summary };
}

export async function postChartsCard(pi: ExtensionAPI, watched: WatchDetails): Promise<void> {
	if (!watched.final) return;
	const runDir = chartsRunDir(watched);
	if (!runDir) return;
	const reportPath = watched.report?.markdown ?? join(runDir, "report.md");
	let details: ChartsDetails;
	try {
		details = await loadChartsDetails(runDir, reportPath);
	} catch {
		return;
	}
	if (!details.images.length && !details.summary.length) return;
	const firstLine = details.summary[0] ?? `charts: ${runDir}/charts/`;
	pi.sendMessage<ChartsDetails>({
		customType: CHARTS_CARD_TYPE,
		content: firstLine.slice(0, 300),
		display: true,
		details,
	});
}

export function renderChartsCard(
	details: ChartsDetails,
	expanded: boolean,
	theme: { fg(color: string, text: string): string },
): Component {
	const lines = [...(details.summary ?? [])];
	if (details.reportPath) lines.push(`report: ${details.reportPath}`);
	if (!details.images?.length) {
		lines.push("no chart PNGs; open the report markdown instead");
		return new Text(lines.join("\n"), 0, 0);
	}
	const shown = expanded ? details.images : details.images.slice(0, 1);
	const card = new Container();
	card.addChild(new Text(lines.join("\n"), 0, 0));
	for (const image of shown) {
		card.addChild(new Image(image.base64, "image/png", theme as never, { maxWidthCells: 80, maxHeightCells: 24 }));
	}
	return card;
}
