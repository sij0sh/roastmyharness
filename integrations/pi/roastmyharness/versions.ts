import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { roastBinary } from "./core.ts";

export const EXPECTED_ENGINE_VERSION = "0.1.1";
export const EXPECTED_ADAPTER_PROTOCOL = 1;

export type EngineStatus =
	| { kind: "ok"; version: string }
	| { kind: "missing"; hint: string }
	| { kind: "mismatch"; found: string; hint: string };

export const UV_ENGINE_INSTALL =
	'uv tool install --from git+https://github.com/sij0sh/roastmyharness roastmyharness';
export const UV_ENGINE_UPGRADE = 'uv tool upgrade roastmyharness --from git+https://github.com/sij0sh/roastmyharness';

function parseVersion(stdout: string): { version: string; adapterProtocol?: unknown } | null {
	try {
		const parsed = JSON.parse(stdout) as { ok?: unknown; version?: unknown; adapter_protocol?: unknown };
		if (parsed.ok === true && typeof parsed.version === "string") {
			return { version: parsed.version, adapterProtocol: parsed.adapter_protocol };
		}
	} catch {}
	const match = /roastmyharness\s+(\S+)/.exec(stdout.trim());
	return match?.[1] ? { version: match[1] } : null;
}

export async function checkEngine(host: Pick<ExtensionAPI, "exec">): Promise<EngineStatus> {
	let result;
	try {
		result = await host.exec(roastBinary(), ["_bridge", "version"], { timeout: 15_000 });
	} catch {
		return { kind: "missing", hint: `Engine not found. Install it: ${UV_ENGINE_INSTALL}` };
	}
	if (result.code !== 0) {
		return { kind: "missing", hint: `Engine call failed. Reinstall it: ${UV_ENGINE_INSTALL}` };
	}
	const found = parseVersion(result.stdout);
	if (!found) {
		return { kind: "missing", hint: `Engine returned no version. Reinstall it: ${UV_ENGINE_INSTALL}` };
	}
	if (found.version !== EXPECTED_ENGINE_VERSION) {
		return {
			kind: "mismatch",
			found: found.version,
			hint: `Engine ${found.version} differs from extension ${EXPECTED_ENGINE_VERSION}. Update it: ${UV_ENGINE_UPGRADE}`,
		};
	}
	if (found.adapterProtocol !== undefined && found.adapterProtocol !== EXPECTED_ADAPTER_PROTOCOL) {
		return {
			kind: "mismatch",
			found: found.version,
			hint: `Engine adapter protocol ${String(found.adapterProtocol)} differs from extension ${EXPECTED_ADAPTER_PROTOCOL}. Update it: ${UV_ENGINE_UPGRADE}`,
		};
	}
	return { kind: "ok", version: found.version };
}
