import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
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
			"Verify every local path with read-only tools before writing the TOML.",
	);
	return lines.join("\n");
}

export default function (pi: ExtensionAPI) {
	let wizardRunning = false;

	const hideSubmitTool = () => {
		const active = pi.getActiveTools();
		if (active.includes(SUBMIT_TOOL)) pi.setActiveTools(active.filter((name) => name !== SUBMIT_TOOL));
	};
	const showSubmitTool = () => {
		const active = pi.getActiveTools();
		if (!active.includes(SUBMIT_TOOL)) pi.setActiveTools([...active, SUBMIT_TOOL]);
	};

	pi.on("session_start", () => hideSubmitTool());

	pi.registerTool({
		name: SUBMIT_TOOL,
		label: "Submit roast experiment",
		description: "Validate a wizard-authored experiment TOML and launch it with live progress. Only valid during the active /roastmyharness wizard.",
		parameters: Type.Object({ spec_path: Type.String({ description: "Experiment TOML path the session wrote." }) }),
		execute: async (_id, params, signal, onUpdate, ctx) => {
			if (!wizardRunning) {
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
			const confirmed = ctx.hasUI
				? await ctx.ui.confirm("RoastMyHarness", `Launch ${prepared.experiment?.trials ?? "?"} trials?`)
				: true;
			if (!confirmed) {
				return { content: [{ type: "text", text: "launch cancelled by user" }], details: prepared };
			}
			ctx.ui.setStatus(WIDGET_ID, `running plan ${planId}`);
			try {
				const watched = await streamBridgeRun(["_bridge", "run", planId], planId, signal, (text, details) => {
					onUpdate?.({ content: [{ type: "text", text }], details });
				});
				postRunCard(pi, watched);
				return { content: [{ type: "text", text: `experiment ${watched.experiment_id}: ${watched.state}` }], details: watched };
			} finally {
				ctx.ui.setStatus(WIDGET_ID, undefined);
			}
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
			if (wizardRunning) {
				ctx.ui.notify("The RoastMyHarness wizard is already open.", "warning");
				return;
			}
			wizardRunning = true;
			showSubmitTool();
			try {
				const collected = await collectWizard(pi, args, ctx);
				if (!collected) {
					ctx.ui.notify("RoastMyHarness wizard cancelled.", "info");
					return;
				}
				const { answers, stagedNote } = collected;
				const specPath = `${ctx.cwd}/.pi-files/roastmyharness/${answers.experimentName}.toml`;
				ctx.ui.notify("Wizard answers collected. Write the TOML, then submit it.", "info");
				await pi.sendUserMessage(requestText(answers, stagedNote, specPath));
			} finally {
				ctx.ui.setStatus(WIDGET_ID, undefined);
				hideSubmitTool();
				wizardRunning = false;
			}
		},
	});

	pi.registerMessageRenderer(RUN_CARD_TYPE, (message, options, theme) =>
		renderRunCard(message.details as never, options.expanded, theme as never));
}
