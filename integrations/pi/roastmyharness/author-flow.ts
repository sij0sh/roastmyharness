import { writeFile } from "node:fs/promises";
import type {
	AgentToolResult,
	AgentToolUpdateCallback,
	ExtensionAPI,
	ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { keyHint, withFileMutationQueue } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import {
	AUTHOR_ACTIVITY_LIMIT,
	AUTHOR_OUTPUT_LIMIT,
	TOOL_NAME,
	buildArgs,
	addUsage,
	clearActiveRun,
	emptyUsage,
	finalText,
	formatElapsed,
	formatUsage,
	getActiveRun,
	runRoastJson,
	setActiveRun,
	summarize,
	type ActiveRun,
	type AuthorDetails,
	type AvailabilityInfo,
	type CatalogResponse,
	type RoastResponse,
	type ThemeLike,
} from "./core.ts";
import {
	renderWatchResult,
	startExperiment,
	streamStartedExperiment,
} from "./watch.ts";
import { authorCardBg, cardBox, postAuthorCard, postRunCard, type CardTheme } from "./cards.ts";
import type { Component } from "@earendil-works/pi-tui";
import {
	appendActivity,
	authorUpdate,
	buildReviewSummary,
	choiceMismatch,
	compactText,
	fetchAvailability,
	prepareProblem,
	runAuthorChild,
	type AuthorRequest,
	type WizardAnswers,
} from "./author-support.ts";
import { collectWizard, type AuthorOutcome } from "./author-wizard.ts";

export function renderAuthorResult(
	details: AuthorDetails,
	{ expanded, isPartial }: { expanded: boolean; isPartial: boolean },
	theme: ThemeLike,
): Text {
	const running = isPartial && !details.final;
	const validating = details.phase === "validating" || details.phase === "authoring";
	const icon = details.phase === "ready"
		? theme.fg("success", "[OK]")
		: details.phase === "needs_input"
			? theme.fg("warning", "[!]")
			: details.phase === "cancelled"
				? theme.fg("muted", "[-]")
				: theme.fg("accent", running ? "[~]" : "[-]");
	const label = details.phase === "ready"
		? "READY FOR APPROVAL"
		: details.phase === "needs_input"
			? "NEEDS INPUT"
			: details.phase.toUpperCase();
	let text = `${icon} ${theme.fg("toolTitle", theme.bold("Spec author"))}` +
		theme.fg(details.phase === "ready" ? "success" : "muted", ` · ${label}`);
	if (details.spec_path) text += `\n  ${theme.fg("dim", details.spec_path)}`;
	const telemetry: string[] = [];
	if (details.model) telemetry.push(`model ${details.model}`);
	if (details.attempt > 0) telemetry.push(`attempt ${details.attempt}`);
	if (details.elapsed_sec !== undefined) telemetry.push(formatElapsed(details.elapsed_sec));
	const usageLine = formatUsage(details.usage);
	if (usageLine) telemetry.push(usageLine);
	if (telemetry.length) text += `\n  ${theme.fg("dim", telemetry.join(" · "))}`;

	const activityLimit = expanded ? AUTHOR_ACTIVITY_LIMIT : 5;
	const shown = details.activities.slice(-activityLimit);
	const hidden = details.activities.length - shown.length;
	if (hidden > 0) text += `\n  ${theme.fg("muted", `... ${hidden} earlier steps`)}`;
	for (const activity of shown) text += `\n  ${theme.fg("muted", "-> ")}${activity}`;

	if (details.prepared?.experiment) {
		const experiment = details.prepared.experiment;
		text += `\n  ${theme.fg("accent", `${experiment.trials} trials`)}` +
			theme.fg("muted", ` · ${experiment.tasks} tasks x ${experiment.arms} arms x ${experiment.repetitions ?? 1} reps`) +
			theme.fg("dim", ` · max ${experiment.max_parallel} parallel`);
		text += `\n  ${theme.fg("muted", "model ")}${experiment.model}`;
		if (experiment.thinking) text += theme.fg("dim", ` · ${experiment.thinking}`);
		if (experiment.resolved_pi_version) {
			text += theme.fg("dim", ` · pi ${experiment.resolved_pi_version}`);
		}
		if (details.review?.eval) {
			text += `\n  ${theme.fg("muted", "eval ")}${details.review.eval}`;
		}
		if (details.review?.preset) {
			text += `\n  ${theme.fg("muted", "preset ")}${details.review.preset}`;
		}
		if (details.review) {
			text += `\n  ${theme.fg("muted", "tasks ")}${details.review.mix}`;
			text += `\n  ${theme.fg("muted", "estimate ")}${details.review.estimate}`;
		}
		if (experiment.arm_ids?.length) {
			text += `\n  ${theme.fg("muted", "arms ")}${experiment.arm_ids.join(", ")}`;
		}
		if (expanded) {
			for (const [variant, sources] of Object.entries(experiment.variant_sources ?? {})) {
				text += `\n    ${theme.fg("accent", variant)}: ${sources.join(", ") || "no source"}`;
			}
		}
		if (experiment.control && experiment.control !== "excluded") {
			text += `\n  ${theme.fg("muted", "control ")}${experiment.control}`;
		}
		if (details.availability) {
			text += `\n  ${theme.fg("muted", "history ")}${formatAvailability(details.availability)}`;
		}
		if (experiment.hypothesis) {
			text += `\n  ${theme.fg("muted", "hypothesis ")}${compactText(experiment.hypothesis, expanded ? 500 : 160)}`;
		}
	}
	for (const warning of details.prepared?.warnings ?? []) {
		text += `\n  ${theme.fg("warning", `warning: ${warning}`)}`;
	}
	if (!validating) {
		for (const question of details.prepared?.questions ?? []) {
			text += `\n  ${theme.fg("warning", `${question.field}: ${compactText(question.message, 220)}`)}`;
		}
	}
	if (details.output) {
		const lines = details.output.trim().split("\n");
		const visible = expanded ? lines : lines.slice(-4);
		text += `\n${visible.map((line) => `  ${theme.fg("toolOutput", line)}`).join("\n")}`;
	}
	if (expanded && details.spec_preview) {
		text += `\n${theme.fg("muted", "  --- Spec preview ---")}`;
		text += `\n${details.spec_preview.split("\n").map((line) => `  ${theme.fg("dim", line)}`).join("\n")}`;
	}
	if (!expanded && details.final && details.spec_preview) {
		text += `\n  ${theme.fg("muted", keyHint("app.tools.expand", "to show spec"))}`;
	}
	return new Text(text, 0, 0);
}

function formatAvailability(info: AvailabilityInfo): string {
	if (!info.available) return `unavailable: ${compactText(info.reason ?? "unknown", 160)}`;
	const age = (info.age_range ?? []).filter(Boolean).join("-");
	const bits = [`${info.eligible ?? "?"}/${info.total ?? "?"} eligible`];
	if (info.total_samples !== undefined) bits.push(`${info.total_samples} samples`);
	if (age) bits.push(`age ${age}`);
	if (info.sentinel_tasks?.length) bits.push(`${info.sentinel_tasks.length} sentinels`);
	if (info.history_scope) bits.push(`scope ${info.history_scope}`);
	if (info.status) bits.push(info.status);
	return bits.join(" · ");
}

/**
 * Stamp final-review facts onto the author details: preset/task-mix/estimate
 * from the wizard's catalog snapshot, plus live history coverage for
 * historic controls. Runs after authorLoop, before the plan is presented.
 */
async function attachReview(
	pi: ExtensionAPI,
	collected: { answers: WizardAnswers; catalog: CatalogResponse | null },
	details: AuthorDetails,
	specPath: string,
): Promise<void> {
	details.review = buildReviewSummary(collected.answers, collected.catalog, details.prepared?.experiment);
	details.availability = collected.answers.control === "historic"
		? (await fetchAvailability(pi, specPath)) ?? undefined
		: undefined;
}

/**
 * Transcript-card entry point for posted author cards: same rendering as
 * the roast_harness tool card, driven by the persisted card payload, in
 * the same colored container native tool cards use.
 */
export function renderAuthorCard(details: unknown, expanded: boolean, theme: CardTheme): Component {
	if (!details || typeof details !== "object" || (details as AuthorDetails).kind !== "author") {
		return new Text("(no author data)", 0, 0);
	}
	const typed = details as AuthorDetails;
	return cardBox(theme, authorCardBg(typed), renderAuthorResult(typed, { expanded, isPartial: false }, theme));
}

async function authorLoop(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	answers: WizardAnswers,
	request: AuthorRequest,
	signal: AbortSignal | undefined,
	onUpdate: AgentToolUpdateCallback<AuthorDetails> | undefined,
	skipDocker: boolean,
): Promise<AuthorOutcome> {
	const details: AuthorDetails = {
		kind: "author",
		phase: "starting",
		final: false,
		spec_path: request.output_path,
		attempt: 0,
		activities: [],
		output: "Starting an isolated Pi author...",
	};
	const usage = emptyUsage();
	const loopStart = Date.now();
	onUpdate?.(authorUpdate(details));

	let prepared: RoastResponse | undefined;
	let specText = "";
	for (let attempt = 1; attempt <= 3; attempt++) {
		details.attempt = attempt;
		details.phase = "authoring";
		details.output = "";
		details.prepared = undefined;
		if (attempt > 1) {
			const previous = request.validation_problem ?? "validation failed";
			appendActivity(details, `Repair spec (attempt ${attempt}): ${compactText(previous, 160)}`);
		} else {
			appendActivity(details, "Draft experiment spec");
		}
		onUpdate?.(authorUpdate(details));

		try {
			specText = await runAuthorChild(ctx, request, signal, onUpdate, details, usage);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			appendActivity(details, `Author attempt ${attempt} failed: ${compactText(message, 160)}`);
			details.output = message.slice(-AUTHOR_OUTPUT_LIMIT);
			onUpdate?.(authorUpdate(details));
			throw error;
		}
		await withFileMutationQueue(request.output_path, async () => {
			await writeFile(request.output_path, specText, { encoding: "utf8", mode: 0o600 });
		});
		details.spec_preview = specText.slice(0, AUTHOR_OUTPUT_LIMIT);
		details.phase = "validating";
		appendActivity(details, "Validate the generated experiment");
		onUpdate?.(authorUpdate(details));

		prepared = await runRoastJson(
			pi,
			buildArgs({
				action: "prepare",
				spec_path: request.output_path,
				skip_docker: skipDocker || undefined,
			}),
			{ signal },
		);
		details.prepared = prepared;
		// Every needs_input question prepare emits (spec, tasks.path, preflight.*)
		// is repairable by the author; missing this dead-ends after one attempt.
		const specProblem = prepared.state === "needs_input" && (prepared.questions ?? []).length > 0;
		const mismatch = choiceMismatch(prepared, answers);
		if (!specProblem && !mismatch) break;
		if (attempt === 3) {
			if (mismatch) {
				prepared = {
					...prepared,
					ok: false,
					state: "needs_input",
					plan_id: undefined,
					questions: [{ field: "wizard", message: mismatch, choices: [] }],
				};
				details.prepared = prepared;
			}
			break;
		}
		request = {
			...request,
			current_spec: specText,
			validation_problem: specProblem ? prepareProblem(prepared) : mismatch,
		};
	}

	if (!prepared) throw new Error("Spec validation returned no result");
	details.usage = usage;
	details.elapsed_sec = (Date.now() - loopStart) / 1000;
	return { prepared, request, spec_text: specText, details, usage };
}

export async function authorExperiment(
	pi: ExtensionAPI,
	taskRoot: string,
	ctx: ExtensionContext,
	signal: AbortSignal | undefined,
	onUpdate: AgentToolUpdateCallback<AuthorDetails> | undefined,
	skipDocker: boolean,
): Promise<AgentToolResult<AuthorDetails>> {
	const collected = await collectWizard(pi, taskRoot, ctx);
	if (!collected) {
		return {
			content: [{ type: "text", text: "Spec authoring cancelled by user" }],
			details: {
				kind: "author",
				phase: "cancelled",
				final: true,
				attempt: 0,
				activities: [],
				output: "Wizard cancelled.",
			},
		};
	}

	const { prepared, request, details, usage } = await authorLoop(
		pi,
		ctx,
		collected.answers,
		collected.request,
		signal,
		onUpdate,
		skipDocker,
	);
	await attachReview(pi, collected, details, request.output_path);
	details.final = true;
	details.phase = prepared.state === "ready_for_confirmation" ? "ready" : "needs_input";
	details.output = prepared.state === "ready_for_confirmation"
		? "Spec is valid. Review the plan and approve it before launch."
		: prepareProblem(prepared) || summarize(prepared);
	onUpdate?.(authorUpdate(details));
	return {
		content: [{
			type: "text",
			text: JSON.stringify({
				state: prepared.state,
				plan_id: prepared.plan_id,
				spec_path: request.output_path,
				experiment: prepared.experiment,
				warnings: prepared.warnings,
				questions: prepared.questions,
				next_action: prepared.state === "ready_for_confirmation"
					? "Present this plan and wait for explicit user approval before calling start."
					: prepared.next_action,
			}, null, 2),
		}],
		details: { ...details, activities: [...details.activities] },
		usage,
	};
}

export const WIDGET_ID = "roastmyharness";

async function presentPlan(
	ctx: ExtensionContext,
	details: AuthorDetails,
	ready: boolean,
): Promise<"launch" | "regenerate" | "cancel"> {
	ctx.ui.setStatus(WIDGET_ID, undefined);
	const planBg = ready ? "toolSuccessBg" as const : "toolPendingBg" as const;
	ctx.ui.setWidget(
		WIDGET_ID,
		(_tui, theme) =>
			cardBox(
				theme,
				planBg,
				renderAuthorResult(details, { expanded: true, isPartial: false }, theme),
			),
	);
	const choice = await ctx.ui.select(
		ready
			? "RoastMyHarness plan ready - Confirm and launch is the default"
			: "RoastMyHarness spec needs changes",
		ready
			? ["Confirm and launch", "Regenerate with feedback", "Cancel"]
			: ["Regenerate with feedback", "Cancel"],
	);
	ctx.ui.setWidget(WIDGET_ID, undefined);
	if (choice === "Confirm and launch") return "launch";
	if (choice === "Regenerate with feedback") return "regenerate";
	return "cancel";
}

/**
 * Launch an approved plan, then return immediately so the Pi prompt box
 * stays live. Progress streams into the widget card from a background
 * watcher; the user can keep chatting (ask for updates, cancel via the
 * roast_harness tool) or re-run /roastmyharness for the run menu.
 * Returns the experiment id, or undefined when launch failed.
 */
async function launchExperiment(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	planId: string,
): Promise<string | undefined> {
	ctx.ui.setStatus(WIDGET_ID, `launching plan ${planId}...`);
	let started: RoastResponse;
	try {
		started = await startExperiment(pi, planId);
	} catch (error) {
		ctx.ui.setStatus(WIDGET_ID, undefined);
		ctx.ui.notify(
			`failed to launch plan ${planId}: ${error instanceof Error ? error.message : String(error)}`,
			"error",
		);
		return undefined;
	}
	if (!started.experiment_id) {
		ctx.ui.setStatus(WIDGET_ID, undefined);
		ctx.ui.notify(summarize(started).slice(0, 4000), "warning");
		return undefined;
	}
	trackRun(pi, ctx, started.experiment_id, planId);
	return started.experiment_id;
}

function ensureToolVisible(pi: ExtensionAPI): void {
	const active = pi.getActiveTools();
	if (!active.includes(TOOL_NAME)) pi.setActiveTools([...active, TOOL_NAME]);
}

/**
 * Register the run and stream its progress into the widget card without
 * blocking the command: the returned promise settles in the background.
 * The roast_harness tool stays visible while tracked so the session can
 * answer update/cancel questions about the run.
 */
function trackRun(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	experimentId: string,
	planId: string,
): void {
	const controller = new AbortController();
	const registration: ActiveRun = {
		experiment_id: experimentId,
		plan_id: planId,
		started_at: Date.now(),
		abortWatch: () => controller.abort(),
	};
	setActiveRun(registration);
	ensureToolVisible(pi);
	ctx.ui.setStatus(WIDGET_ID, `running ${experimentId}`);
	// A superseded watcher (replaced by Watch live / a new run) settles as
	// detached; it must not clear the newer registration or its card.
	const current = () => getActiveRun() === registration;
	void streamStartedExperiment(experimentId, {}, controller.signal, (update) => {
		if (!current()) return;
		ctx.ui.setWidget(
			WIDGET_ID,
			(_tui, theme) =>
				cardBox(
					theme,
					"toolPendingBg",
					renderWatchResult(update.details, { expanded: false }, theme),
				),
		);
	}).then((watched) => {
		if (!current()) return;
		postRunCard(pi, watched.details);
		clearActiveRun(experimentId);
		ctx.ui.setWidget(WIDGET_ID, undefined);
		ctx.ui.setStatus(WIDGET_ID, undefined);
		ctx.ui.notify(finalText(watched.details), "info");
	}).catch((error) => {
		if (!current()) return;
		if (controller.signal.aborted) {
			ctx.ui.setStatus(WIDGET_ID, undefined);
			return;
		}
		const message = error instanceof Error ? error.message : String(error);
		postRunCard(pi, {
			stream: true,
			experiment_id: experimentId,
			state: "?",
			final: false,
			ended: true,
			note: `watch failed (it may still be running): ${message}`.slice(0, 500),
			recent: [],
			summaries: [],
		});
		ctx.ui.notify(
			`watch failed for ${experimentId} (it may still be running): ${message}`,
			"warning",
		);
	});
}

function statusLine(response: RoastResponse): string {
	const totals = (response as { totals?: Record<string, Record<string, number>> }).totals;
	const parts = [summarize(response)];
	if (totals) {
		for (const [variant, counts] of Object.entries(totals)) {
			parts.push(`${variant}: P=${counts.P ?? 0} F=${counts.F ?? 0} E=${counts.E ?? 0}`);
		}
	}
	return parts.join("\n").slice(0, 4000);
}

/**
 * Menu shown when /roastmyharness runs while a command-launched experiment
 * is still tracked. Returns true when the caller should continue into the
 * wizard for a new run.
 */
export async function runActiveMenu(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	active: ActiveRun,
): Promise<boolean> {
	const choice = await ctx.ui.select(
		`RoastMyHarness run ${active.experiment_id} is active`,
		["Watch live", "Show status", "Cancel run", "Start a new run"],
	);
	if (choice === "Watch live") {
		trackRun(pi, ctx, active.experiment_id, active.plan_id);
		ctx.ui.notify(`attached live progress for ${active.experiment_id}`, "info");
		return false;
	}
	if (choice === "Show status") {
		try {
			const status = await runRoastJson(
				pi,
				buildArgs({ action: "status", experiment_id: active.experiment_id }),
			);
			ctx.ui.notify(statusLine(status), "info");
		} catch (error) {
			ctx.ui.notify(
				`status failed for ${active.experiment_id}: ` +
					(error instanceof Error ? error.message : String(error)),
				"warning",
			);
		}
		return false;
	}
	if (choice === "Cancel run") {
		try {
			const cancelled = await runRoastJson(
				pi,
				buildArgs({ action: "cancel", experiment_id: active.experiment_id }),
			);
			ctx.ui.notify(statusLine(cancelled), "info");
			const state = (cancelled as { state?: string }).state;
			if (state === "CANCELLED" || state === "COMPLETE" || state === "FAILED") {
				clearActiveRun(active.experiment_id);
			}
		} catch (error) {
			ctx.ui.notify(
				`cancel failed for ${active.experiment_id}: ` +
					(error instanceof Error ? error.message : String(error)),
				"error",
			);
		}
		return false;
	}
	if (choice === "Start a new run") return true;
	return false;
}

export async function runCommandFlow(pi: ExtensionAPI, args: string, ctx: ExtensionContext): Promise<void> {
	const collected = await collectWizard(pi, args, ctx, args.trim());
	if (!collected) {
		ctx.ui.notify("RoastMyHarness wizard cancelled.", "info");
		return;
	}
	let request = collected.request;
	let specText: string | undefined;
	const flowStart = Date.now();
	const flowUsage = emptyUsage();
	const stampFlow = (details: AuthorDetails): void => {
		details.usage = { ...flowUsage, cost: { ...flowUsage.cost } };
		details.elapsed_sec = (Date.now() - flowStart) / 1000;
	};
	while (true) {
		const outcome = await authorLoop(
			pi,
			ctx,
			collected.answers,
			request,
			undefined,
			(update) => {
				ctx.ui.setStatus(WIDGET_ID, `${update.details.phase} (attempt ${update.details.attempt})`);
				ctx.ui.setWidget(
					WIDGET_ID,
					(_tui, theme) =>
						cardBox(
							theme,
							"toolPendingBg",
							renderAuthorResult(update.details, { expanded: true, isPartial: true }, theme),
						),
				);
			},
			false,
		);
		addUsage(flowUsage, outcome.usage);
		request = outcome.request;
		specText = outcome.spec_text;
		await attachReview(pi, collected, outcome.details, outcome.request.output_path);
		const ready = outcome.prepared.state === "ready_for_confirmation" &&
			Boolean(outcome.prepared.plan_id);
		if (!ready) {
			outcome.details.output = prepareProblem(outcome.prepared) || summarize(outcome.prepared);
		}
		const next = await presentPlan(ctx, outcome.details, ready);
		if (next === "launch") {
			outcome.details.phase = "ready";
			outcome.details.final = true;
			stampFlow(outcome.details);
			postAuthorCard(pi, outcome.details);
			const experimentId = await launchExperiment(pi, ctx, outcome.prepared.plan_id as string);
			if (experimentId) {
				ctx.ui.notify(
					`experiment ${experimentId} running — the prompt box stays live; ` +
						`ask the session for updates or re-run /roastmyharness to watch, check status, or cancel.`,
					"info",
				);
			}
			return;
		}
		if (next === "cancel") {
			outcome.details.phase = ready ? "ready" : "needs_input";
			outcome.details.final = true;
			stampFlow(outcome.details);
			postAuthorCard(pi, outcome.details);
			ctx.ui.notify(
				ready ? `Plan kept on disk: ${request.output_path}` : "RoastMyHarness cancelled.",
				"info",
			);
			return;
		}
		const feedback = await ctx.ui.input(
			"What should change in the experiment spec?",
			"Freeform feedback; the author will revise the current spec",
		);
		if (feedback === undefined || !feedback.trim()) {
			ctx.ui.notify("RoastMyHarness cancelled.", "info");
			return;
		}
		request = {
			...collected.request,
			current_spec: specText,
			validation_problem: feedback.trim(),
		};
	}
}
