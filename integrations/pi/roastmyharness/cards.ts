import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Box, type Component, Text } from "@earendil-works/pi-tui";
import { finalText, type ThemeLike, type WatchDetails } from "./core.ts";

export interface CardTheme extends ThemeLike {
	bg(color: string, text: string): string;
}

type CardBg = "toolSuccessBg" | "toolPendingBg" | "toolErrorBg";

export function runCardBg(details: WatchDetails): CardBg {
	if (details.state === "COMPLETE") return "toolSuccessBg";
	if (details.state === "FAILED" || details.state === "CANCELLED") return "toolErrorBg";
	return "toolPendingBg";
}

export function cardBox(theme: CardTheme, bg: CardBg | undefined, text: Text): Component {
	if (!bg) return text;
	const box = new Box(1, 1, (line) => theme.bg(bg, line));
	box.addChild(text);
	return box;
}

export const RUN_CARD_TYPE = "roastmyharness-run";

export function postRunCard(pi: ExtensionAPI, details: WatchDetails): void {
	const firstLine = finalText(details).split("\n")[0] ?? details.experiment_id;
	pi.sendMessage<WatchDetails>({
		customType: RUN_CARD_TYPE,
		content: firstLine.slice(0, 300),
		display: true,
		details: { ...details, recent: [...details.recent], summaries: [...details.summaries] },
	});
}
