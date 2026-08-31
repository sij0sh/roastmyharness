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
	buildArgs,
	emptyUsage,
	finalText,
	roastBinary,
	summarize,
	type AuthorDetails,
	type RoastResponse,
	type ThemeLike,
} from "./core.ts";
import {
	renderWatchResult,
	streamStartedExperiment,
} from "./watch.ts";
import {
	appendActivity,
	authorUpdate,
	choiceMismatch,
	compactText,
	prepareProblem,
	runAuthorChild,
	runRoastJson,
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

	const activityLimit = expanded ? AUTHOR_ACTIVITY_LIMIT : 5;
	const shown = details.activities.slice(-activityLimit);
	const hidden = details.activities.length - shown.length;
	if (hidden > 0) text += `\n  ${theme.fg("muted", `... ${hidden} earlier steps`)}`;
	for (const activity of shown) text += `\n  ${theme.fg("muted", "-> ")}${activity}`;

	if (details.prepared?.experiment) {
		const experiment = details.prepared.experiment;
		text += `\n  ${theme.fg("accent", `${experiment.trials} trials`)}` +
			theme.fg("muted", ` · ${experiment.tasks} tasks x ${experiment.arms} arms`) +
			theme.fg("dim", ` · max ${experiment.max_parallel} parallel`);
		text += `\n  ${theme.fg("muted", "model ")}${experiment.model}`;
		if (experiment.thinking) text += theme.fg("dim", ` · ${experiment.thinking}`);
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

		const prepareArgs = ["tool", "prepare", request.output_path];
		if (skipDocker) prepareArgs.push("--skip-docker");
		prepared = await runRoastJson(pi, prepareArgs, signal);
		details.prepared = prepared;
		const specProblem = prepared.state === "needs_input" &&
			(prepared.questions ?? []).some((question) =>
				/^(spec|variants?|tasks?|control|model|pi_version)(\.|$)/.test(question.field));
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
	const collected = await collectWizard(taskRoot, ctx);
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
	ctx.ui.setWidget(
		WIDGET_ID,
		(_tui, theme) => renderAuthorResult(details, { expanded: true, isPartial: false }, theme),
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

async function launchExperiment(pi: ExtensionAPI, ctx: ExtensionContext, planId: string): Promise<void> {
	ctx.ui.setStatus(WIDGET_ID, `launching plan ${planId}...`);
	let result;
	try {
		result = await pi.exec(roastBinary(), buildArgs({ action: "start", plan_id: planId }), {
			timeout: 120_000,
		});
	} catch (error) {
		ctx.ui.setStatus(WIDGET_ID, undefined);
		ctx.ui.notify(
			`failed to run ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`,
			"error",
		);
		return;
	}
	ctx.ui.setStatus(WIDGET_ID, undefined);
	const stdout = result.stdout.trim();
	let parsed: RoastResponse | undefined;
	try {
		if (stdout) parsed = JSON.parse(stdout) as RoastResponse;
	} catch {
	}
	if (result.code !== 0 && parsed?.error) {
		ctx.ui.notify(`error ${parsed.error.code ?? "unknown"}: ${parsed.error.message ?? stdout}`, "error");
		return;
	}
	if (!parsed?.experiment_id) {
		ctx.ui.notify(
			(result.stderr.trim() || stdout || summarize(parsed ?? { state: "unknown" })).slice(0, 4000),
			"warning",
		);
		return;
	}
	const experimentId = parsed.experiment_id;
	try {
		const watched = await streamStartedExperiment(experimentId, {}, undefined, (update) => {
			ctx.ui.setWidget(
				WIDGET_ID,
				(_tui, theme) => renderWatchResult(update.details, { expanded: false }, theme),
			);
		});
		ctx.ui.notify(finalText(watched.details), "info");
	} catch (error) {
		ctx.ui.notify(
			`watch failed for ${experimentId} (it may still be running): ` +
				(error instanceof Error ? error.message : String(error)),
			"warning",
		);
	} finally {
		ctx.ui.setWidget(WIDGET_ID, undefined);
	}
}

export async function runCommandFlow(pi: ExtensionAPI, args: string, ctx: ExtensionContext): Promise<void> {
	const collected = await collectWizard(args, ctx, args.trim());
	if (!collected) {
		ctx.ui.notify("RoastMyHarness wizard cancelled.", "info");
		return;
	}
	let request = collected.request;
	let specText: string | undefined;
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
						renderAuthorResult(update.details, { expanded: true, isPartial: true }, theme),
				);
			},
			false,
		);
		request = outcome.request;
		specText = outcome.spec_text;
		const ready = outcome.prepared.state === "ready_for_confirmation" &&
			Boolean(outcome.prepared.plan_id);
		if (!ready) {
			outcome.details.output = prepareProblem(outcome.prepared) || summarize(outcome.prepared);
		}
		const next = await presentPlan(ctx, outcome.details, ready);
		if (next === "launch") {
			await launchExperiment(pi, ctx, outcome.prepared.plan_id as string);
			return;
		}
		if (next === "cancel") {
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
