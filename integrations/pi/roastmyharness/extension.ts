import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import { Type } from "typebox";
import {
	AWAIT_TOOL,
	SUBMIT_TOOL,
	bridgeArgs,
	runBridgeJson,
	summarize,
	type RoastResponse,
} from "./core.ts";
import { RUN_CARD_TYPE, postRunCard } from "./cards.ts";
import { renderRunCard, streamBridgeRun } from "./watch.ts";
import { collectWizard } from "./wizard.ts";
import type { WizardAnswers } from "./wizard-options.ts";

const WIDGET_ID = "roastmyharness-widget";

const tomlStr = (value: string): string => JSON.stringify(value);

const SPEC_TEMPLATE = (answers: WizardAnswers): string => {
	const include = answers.taskIds.map((id) => `  ${tomlStr(id)},`).join("\n");
	return `schema_version = 3
name = ${tomlStr(answers.experimentName)}

model = ${tomlStr(`${answers.modelProvider}/${answers.modelId}`)}
thinking = ${tomlStr(answers.thinking)}
pi_version = "latest"
${answers.control === "fresh" ? "" : "control = false\n"}
[tasks]
path = ${tomlStr(answers.taskRoot)}
include = [
${include}
]

[execution]
repetitions = ${answers.repetitions}

# Pi adds [[variants]] below from the variant request.
`;
};

function requestText(
	answers: WizardAnswers,
	stagedNote: string,
	specPath: string,
): string {
	const lines = [
		"RoastMyHarness experiment request (from /roastmyharness wizard):",
		`- name: ${answers.experimentName}`,
		`- model: ${answers.modelProvider}/${answers.modelId}`,
		`- thinking: ${answers.thinking}`,
		`- control: ${answers.control}`,
		`- tasks: ${answers.taskLabel} under ${answers.taskRoot}`,
		`- repetitions: ${answers.repetitions}`,
		"",
		`Variant request: ${answers.variantRequest}`,
	];
	if (stagedNote) lines.push("", stagedNote);
	if (answers.control === "historic") {
		lines.push(
			"",
			`Historic baseline covers ${answers.historicCount} tasks; control = false. ` +
				"Record the historic pool in the hypothesis and compare variant results against it.",
		);
	}
	lines.push(
		"",
		`Write this experiment as schema_version = 3 TOML to ${specPath} ` +
			`(draft below), fix any validation problems, then call ` +
			`${SUBMIT_TOOL} with { "spec_path": ${JSON.stringify(specPath)} }.`,
		"",
		"```toml",
		SPEC_TEMPLATE(answers),
		"```",
		"",
		"Derive the experiment name, variant id, variant file locations, and variant " +
			"configuration (extensions, skills, settings, env, pi_flags) from the variant request. " +
			"Verify every local path with read-only tools before writing the TOML. " +
			`Continue without stopping: write the TOML, then call ${SUBMIT_TOOL}. ` +
			"Do not end the turn after writing the TOML.",
	);
	return lines.join("\n");
}

const ANALYSIS_BODY = "per-variant resolve rate (resolved/n) from the aggregates and " +
	"summary.csv; paired flips where control and variant disagree, from the report.md " +
	"matrix; a verdict against the spec hypothesis; and cost/token totals per variant " +
	"from the trial stats. Format: one table per variant (tasks, resolved, rate), " +
	"a discordant-task list, and a one-paragraph verdict.";

const ANALYSIS_GUIDE = `Analyze the run and report back: ${ANALYSIS_BODY}`;

function postRunText(watched: {
	experiment_id: string;
	state: string;
	final: boolean;
	note?: string;
	aggregates?: unknown;
	report?: { markdown: string; csv: string } | null;
}): string {
	const head = `experiment ${watched.experiment_id}: ${watched.state}`;
	if (!watched.final) {
		const note = watched.note ? ` (${watched.note})` : "";
		return `${head} (not final${note}). The run is still going or detached. ` +
			`Call ${AWAIT_TOOL} with { "experiment_id": "${watched.experiment_id}" } and wait for it to return; it blocks with live progress. ` +
			`Do not poll status in a sleep loop. Then analyze the run: ${ANALYSIS_BODY}`;
	}
	const lines = [head];
	if (watched.report) lines.push(`report: ${watched.report.markdown} and ${watched.report.csv}`);
	if (watched.aggregates) lines.push(`aggregates: ${JSON.stringify(watched.aggregates)}`);
	lines.push(ANALYSIS_GUIDE);
	return lines.join("\n");
}

export default function (pi: ExtensionAPI) {
	let wizardState: "idle" | "prompting" | "awaiting-submit" = "idle";

	const wizardTools = [SUBMIT_TOOL, AWAIT_TOOL];
	const hideSubmitTool = () => {
		const active = pi.getActiveTools();
		if (active.some((name) => wizardTools.includes(name))) {
			pi.setActiveTools(active.filter((name) => !wizardTools.includes(name)));
		}
	};
	const showSubmitTool = () => {
		const active = pi.getActiveTools();
		const missing = wizardTools.filter((name) => !active.includes(name));
		if (missing.length) pi.setActiveTools([...active, ...missing]);
	};

	pi.on("session_start", () => {
		wizardState = "idle";
		hideSubmitTool();
	});

	pi.registerTool({
		name: SUBMIT_TOOL,
		label: "Submit roast experiment",
		description: "Validate a wizard-authored experiment TOML and launch it with live progress. Only valid during the active /roastmyharness wizard.",
		parameters: Type.Object({ spec_path: Type.String({ description: "Experiment TOML path the session wrote." }) }),
		execute: async (_id, params, signal, onUpdate, ctx) => {
			if (wizardState === "idle") {
				throw new Error(`${SUBMIT_TOOL} is only valid during the active /roastmyharness wizard`);
			}
			const target = (params as { spec_path: string }).spec_path;
			onUpdate?.({ content: [{ type: "text", text: `validating ${target}` }], details: {} });
			let prepared: RoastResponse;
			try {
				prepared = await runBridgeJson(pi, bridgeArgs("validate", target), { signal });
			} catch (error) {
				return { content: [{ type: "text", text: `validation failed: ${error instanceof Error ? error.message : String(error)}` }], details: {} };
			}
			if (!prepared.ok || !prepared.plan_id) {
				return { content: [{ type: "text", text: summarize(prepared) }], details: prepared };
			}
			const planId = prepared.plan_id;
			let specText = "";
			try {
				specText = await readFile(target, "utf8");
			} catch {
				specText = "";
			}
			if (specText.length > 8000) specText = `${specText.slice(0, 8000)}\n# ... truncated`;
			onUpdate?.({
				content: [{
					type: "text",
					text: specText
						? `Experiment TOML under review (\`${target}\`):\n\`\`\`toml\n${specText}\n\`\`\`\n${summarize(prepared)}`
						: summarize(prepared),
				}],
				details: prepared,
			});
			const confirmed = ctx.hasUI
				? await ctx.ui.confirm("RoastMyHarness", `Launch ${prepared.experiment?.trials ?? "?"} trials as written above? Decline to edit the TOML and resubmit.`)
				: true;
			if (!confirmed) {
				return { content: [{ type: "text", text: "launch cancelled: edit the TOML and call submit_roast_experiment again to redo" }], details: prepared };
			}
			ctx.ui.setStatus(WIDGET_ID, `running plan ${planId}`);
			let launchedFinal = false;
			try {
				const watched = await streamBridgeRun(["_bridge", "run", planId], planId, signal, (text, details) => {
					onUpdate?.({ content: [{ type: "text", text }], details });
				});
				postRunCard(pi, watched);
				launchedFinal = watched.final;
				return { content: [{ type: "text", text: postRunText(watched) }], details: watched };
			} finally {
				ctx.ui.setStatus(WIDGET_ID, undefined);
				if (launchedFinal) {
					wizardState = "idle";
					hideSubmitTool();
				}
			}
		},
	});

	pi.registerTool({
		name: AWAIT_TOOL,
		label: "Wait for roast experiment",
		description: "Block until a roast experiment reaches a final state, with live progress. Use instead of polling status in a sleep loop.",
		parameters: Type.Object({ experiment_id: Type.String({ description: "Experiment id from the submit step." }) }),
		execute: async (_id, params, signal, onUpdate, ctx) => {
			if (wizardState === "idle") {
				throw new Error(`${AWAIT_TOOL} is only valid during the active /roastmyharness wizard`);
			}
			const target = (params as { experiment_id: string }).experiment_id;
			onUpdate?.({ content: [{ type: "text", text: `waiting on ${target}` }], details: {} });
			let watched;
			try {
				watched = await streamBridgeRun(["_bridge", "await", target], target, signal, (text, details) => {
					onUpdate?.({ content: [{ type: "text", text }], details });
				});
			} catch (error) {
				return { content: [{ type: "text", text: `wait failed: ${error instanceof Error ? error.message : String(error)}` }], details: {} };
			}
			postRunCard(pi, watched);
			if (watched.final) {
				wizardState = "idle";
				hideSubmitTool();
				return { content: [{ type: "text", text: postRunText(watched) }], details: watched };
			}
			if (watched.note?.includes("worker not running")) {
				wizardState = "idle";
				hideSubmitTool();
				return {
					content: [{
						type: "text",
						text: `experiment ${watched.experiment_id}: ${watched.state} (${watched.note}). ` +
							"The worker is gone and no new trials will complete. " +
							`Check \`roastmyharness _bridge status ${watched.experiment_id}\` for partial results, then start a new run if needed.`,
					}],
					details: watched,
				};
			}
			return { content: [{ type: "text", text: postRunText(watched) }], details: watched };
		},
	});

	pi.registerCommand("roastmyharness", {
		description: "Configure, validate, and launch a Pi harness comparison",
		handler: async (args: string, ctx: ExtensionContext) => {
			if (!ctx.hasUI) {
				ctx.ui.notify("/roastmyharness requires an interactive Pi session.", "error");
				return;
			}
			if (!ctx.isIdle()) {
				ctx.ui.notify("Wait for the current agent turn to finish.", "warning");
				return;
			}
			if (wizardState === "prompting") {
				ctx.ui.notify("The RoastMyHarness wizard is already open.", "warning");
				return;
			}
			wizardState = "prompting";
			showSubmitTool();
			try {
				const collected = await collectWizard(pi, args, ctx);
				if (!collected) {
					ctx.ui.notify("RoastMyHarness wizard cancelled.", "info");
					wizardState = "idle";
					hideSubmitTool();
					return;
				}
				const { answers, stagedNote } = collected;
				const specPath = `${ctx.cwd}/.pi-files/roastmyharness/${answers.experimentName}.toml`;
				ctx.ui.notify("Wizard answers collected. Write the TOML, then submit it.", "info");
				wizardState = "awaiting-submit";
				await pi.sendUserMessage(requestText(answers, stagedNote, specPath));
			} finally {
				ctx.ui.setStatus(WIDGET_ID, undefined);
			}
		},
	});

	pi.registerMessageRenderer(RUN_CARD_TYPE, (message, options, theme) =>
		renderRunCard(message.details as never, options.expanded, theme as never));
}
