import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Box, type Component, Text } from "@earendil-works/pi-tui";
import {
	finalText,
	type AuthorDetails,
	type ThemeLike,
	type WatchDetails,
} from "./core.ts";

/** Theme surface the card chrome needs (full Pi Theme satisfies this). */
export interface CardTheme extends ThemeLike {
	bg(color: string, text: string): string;
}

type CardBg = "toolSuccessBg" | "toolPendingBg" | "toolErrorBg";

export function authorCardBg(details: AuthorDetails): CardBg | undefined {
	if (details.phase === "ready") return "toolSuccessBg";
	if (details.phase === "needs_input") return "toolPendingBg";
	return undefined;
}

export function runCardBg(details: WatchDetails): CardBg {
	if (details.state === "COMPLETE") return "toolSuccessBg";
	if (details.state === "FAILED" || details.state === "CANCELLED") return "toolErrorBg";
	return "toolPendingBg";
}

/**
 * Wrap rendered card text in the same colored container Pi gives native
 * tool cards (custom-message entries render our component as-is, so the
 * chrome has to come from us).
 */
export function cardBox(theme: CardTheme, bg: CardBg | undefined, text: Text): Component {
	if (!bg) return text;
	const box = new Box(1, 1, (line) => theme.bg(bg, line));
	box.addChild(text);
	return box;
}

/** Transcript card holding a finished spec-author session (static record). */
export const AUTHOR_CARD_TYPE = "roastmyharness-author";
/** Transcript card holding a finished benchmark run (static record). */
export const RUN_CARD_TYPE = "roastmyharness-run";

const AUTHOR_PREVIEW_LIMIT = 4000;
const AUTHOR_OUTPUT_LIMIT = 2000;
const RUN_SUMMARY_LIMIT = 100;
const CONTENT_LIMIT = 300;

function bound(text: string): string {
	return text.length > CONTENT_LIMIT ? `${text.slice(0, CONTENT_LIMIT - 1)}…` : text;
}

function authorContent(details: AuthorDetails): string {
	const plan = details.prepared;
	const trials = plan?.experiment ? `, ${plan.experiment.trials} trials` : "";
	const model = plan?.experiment ? `, model ${plan.experiment.model}` : "";
	if (details.phase === "ready" && plan?.plan_id) {
		return bound(`RoastMyHarness spec ready: plan ${plan.plan_id}${trials}${model}.`);
	}
	const questions = plan?.questions ?? [];
	const first = questions.length ? `: ${questions[0].field}` : "";
	return bound(`RoastMyHarness spec ${details.phase} (${questions.length} open)${first}.`);
}

/**
 * Post a persistent transcript card for a finished author session. The live
 * widget covers streaming; this card is the durable record with the same
 * rendering as the roast_harness tool card. No model call is triggered.
 */
export function postAuthorCard(pi: ExtensionAPI, details: AuthorDetails): void {
	const snapshot: AuthorDetails = {
		...details,
		activities: [...details.activities],
		output: details.output.slice(-AUTHOR_OUTPUT_LIMIT),
		spec_preview: details.spec_preview?.slice(0, AUTHOR_PREVIEW_LIMIT),
		usage: details.usage ? { ...details.usage, cost: { ...details.usage.cost } } : undefined,
		prepared: details.prepared ? { ...details.prepared } : undefined,
	};
	pi.sendMessage<AuthorDetails>({
		customType: AUTHOR_CARD_TYPE,
		content: authorContent(details),
		display: true,
		details: snapshot,
	});
}

/**
 * Post a persistent transcript card for a finished (or failed-to-watch)
 * benchmark run, rendered like the roast_harness watch card. No model
 * call is triggered.
 */
export function postRunCard(pi: ExtensionAPI, details: WatchDetails): void {
	const firstLine = finalText(details).split("\n")[0] ?? details.experiment_id;
	const snapshot: WatchDetails = {
		...details,
		running: details.running ? [...details.running] : undefined,
		recent: [...details.recent],
		summaries: details.summaries.slice(-RUN_SUMMARY_LIMIT),
		aggregates: details.aggregates ? { ...details.aggregates } : undefined,
		matrix: details.matrix ? { ...details.matrix } : undefined,
		totals: details.totals ? { ...details.totals } : undefined,
	};
	pi.sendMessage<WatchDetails>({
		customType: RUN_CARD_TYPE,
		content: bound(firstLine),
		display: true,
		details: snapshot,
	});
}
