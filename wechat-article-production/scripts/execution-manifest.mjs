#!/usr/bin/env node

import { readFile, stat } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { isAbsolute, join, relative, resolve } from "node:path";
import { homedir } from "node:os";
import { parseArgs, required, list, packageRelativePath, atomicWrite } from "./shared.mjs";

const STAGES = ["prepare", "format", "cover", "illustrate", "typeset", "validate", "publish"];
const DEFAULT_ILLUSTRATION_SKILL = "jiyi-little-dancer-illustrations";
const DOWNSTREAM_SKILLS = new Set([
  "baoyu-format-markdown",
  "baoyu-cover-image",
  "baoyu-article-illustrator",
  "jiyi-little-dancer-illustrations",
  "gzh-design",
  "gzh-design-skill",
]);
const ILLUSTRATION_SKILLS = new Set([
  "baoyu-article-illustrator",
  "jiyi-little-dancer-illustrations",
]);
const COVER_TEXT_LEVELS = new Set(["none", "title-only", "title-subtitle", "text-rich"]);

function jsonValue(value, fallback) {
  if (value === undefined) return fallback;
  return typeof value === "string" ? JSON.parse(value) : value;
}

function booleanArg(value, fallback = false) {
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function assertSafeAbsolutePath(value, name) {
  if (typeof value !== "string" || value === "") throw new Error(`Missing --${name}`);
  if (!isAbsolute(value)) throw new Error(`--${name} must be an absolute path: ${value}`);
  if (/\0|[\r\n]/.test(value)) throw new Error(`--${name} contains a control character`);
  if (/^\s*[\[{]/.test(value) || /["']package_dir["']\s*:/.test(value)) {
    throw new Error(`--${name} looks like serialized JSON, not a filesystem path; parse .package_dir first`);
  }
  return resolve(value);
}

function coverTextOverride(value) {
  if (value === undefined || value === "") return null;
  if (!COVER_TEXT_LEVELS.has(value)) {
    throw new Error(`--cover-text-override must be one of: ${[...COVER_TEXT_LEVELS].join(", ")}`);
  }
  return value;
}

async function loadCoverTextPreference(packageDir) {
  const configRoot = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
  const candidates = [
    join(packageDir, ".baoyu-skills", "baoyu-cover-image", "EXTEND.md"),
    join(configRoot, "baoyu-skills", "baoyu-cover-image", "EXTEND.md"),
    join(homedir(), ".baoyu-skills", "baoyu-cover-image", "EXTEND.md"),
  ];
  for (const file of candidates) {
    try {
      const content = await readFile(file, "utf8");
      const match = content.match(/^preferred_text:\s*["']?([^\s#"']+)["']?/m);
      if (match && COVER_TEXT_LEVELS.has(match[1])) return { value: match[1], source: file };
      return { value: null, source: file };
    } catch {
      // Continue through the documented preference precedence.
    }
  }
  return { value: null, source: null };
}

function promptTextLevel(content) {
  const frontmatter = content.match(/^(?:---\s*\n)([\s\S]*?)(?:\n---\s*\n)/);
  const match = frontmatter?.[1]?.match(/^text:\s*["']?([^\s#"']+)["']?/m);
  return match?.[1] || null;
}

function hasPositiveTitleInstruction(content) {
  const body = content.replace(/^(?:---\s*\n)[\s\S]*?(?:\n---\s*\n)/, "");
  return /(?:exact title text|title text|中文标题|写入标题|render\s+(?:the\s+)?title|Title:\s*[^Nn]o)/i.test(body);
}

async function assertCoverTextPolicy(packageDir, outputs, manifest) {
  const prompt = outputs.find((value) => value.startsWith("cover/prompts/") && /\.(?:md|txt)$/i.test(value));
  if (!prompt) return;
  const content = await readFile(resolve(packageDir, prompt), "utf8");
  const override = manifest.scope?.cover_text_override || null;
  const configured = override ? { value: override, source: "current-request-override" } : await loadCoverTextPreference(packageDir);
  if (!configured.value) return;
  const actual = promptTextLevel(content);
  if (actual !== configured.value) {
    throw new Error(`Cover prompt text level must follow ${configured.source}: expected ${configured.value}, received ${actual || "missing"}`);
  }
  if (configured.value === "none" && hasPositiveTitleInstruction(content)) {
    throw new Error("Cover prompt is configured as text=none but contains a positive title instruction");
  }
}

async function assertCoverPromptValidation(packageDir, outputs) {
  const report = outputs.find((value) => value === "cover/prompt-validation.json");
  if (!report) throw new Error("Cover stage requires cover/prompt-validation.json");
  let parsed;
  try {
    parsed = JSON.parse(await readFile(resolve(packageDir, report), "utf8"));
  } catch (error) {
    throw new Error(`Cannot read cover prompt validation report: ${error.message}`);
  }
  if (parsed.passed !== true) throw new Error("Cover prompt validation report must have passed=true");
}

async function assertPassedReport(packageDir, relativePath, label) {
  let report;
  try {
    report = JSON.parse(await readFile(resolve(packageDir, relativePath), "utf8"));
  } catch (error) {
    throw new Error(`Cannot read ${label}: ${error.message}`);
  }
  if (report.passed !== true) throw new Error(`${label} must have passed=true`);
}

async function assertStageHandoff(manifest, stage, outputs) {
  const required = new Set();
  if (stage === "prepare") {
    required.add("source.md");
    required.add("execution-manifest.json");
  } else if (stage === "format") {
    required.add("analysis.md");
    required.add("formatted.md");
    required.add("format-validation.json");
  } else if (stage === "cover") {
    required.add("cover/cover.png");
    required.add("cover/prompt-validation.json");
    if (!outputs.some((value) => /^cover\/prompts\/[^/]+\.(?:md|txt)$/i.test(value))) {
      throw new Error("Cover stage requires at least one prompt file under cover/prompts/");
    }
  } else if (stage === "illustrate") {
    required.add("illustrations/outline.md");
    required.add("article-illustrated.md");
    if (manifest.stages.find((item) => item.id === "illustrate")?.skill === "jiyi-little-dancer-illustrations") {
      required.add(manifest.scope?.illustration_reference || "illustrations/ip-reference.png");
      required.add("illustrations/outline-validation.json");
      required.add("illustrations/prompt-validation.json");
      required.add("illustrations/image-validation.json");
      required.add("illustrations/illustration-handoff.json");
      if (!outputs.some((value) => /^illustrations\/(?!ip-reference\.png$)[^/]+\.(?:png|jpe?g|webp)$/i.test(value))) {
        throw new Error("Little-dancer illustrate stage requires at least one generated image output");
      }
    }
  } else if (stage === "typeset") {
    required.add("article.html");
    required.add("article-preview.html");
  } else if (stage === "validate") {
    required.add("validation-report.json");
  } else if (stage === "publish") {
    required.add("publish-result.json");
  }
  const missing = [...required].filter((output) => !outputs.includes(output));
  if (missing.length) throw new Error(`${stage} handoff is incomplete; missing declared outputs: ${missing.join(", ")}`);

  if (stage === "format") await assertPassedReport(manifest.package_dir, "format-validation.json", "format-validation.json");
  if (stage === "illustrate" && manifest.stages.find((item) => item.id === "illustrate")?.skill === "jiyi-little-dancer-illustrations") {
    await assertPassedReport(manifest.package_dir, "illustrations/outline-validation.json", "outline-validation.json");
    await assertPassedReport(manifest.package_dir, "illustrations/prompt-validation.json", "prompt-validation.json");
    await assertPassedReport(manifest.package_dir, "illustrations/image-validation.json", "image-validation.json");
  }
}

async function readManifest(file) {
  return JSON.parse(await readFile(file, "utf8"));
}



async function normalizeAndCheckOutputs(packageDir, outputs) {
  const normalized = outputs.map((output) => packageRelativePath(packageDir, output));
  const missing = [];
  for (const output of normalized) {
    const target = resolve(packageDir, output);
    try {
      const info = await stat(target);
      if (!info.isFile()) missing.push(`${output} (not a file)`);
      else if (info.size === 0) missing.push(`${output} (empty)`);
    } catch {
      missing.push(output);
    }
  }
  if (missing.length) throw new Error(`Declared outputs are missing or empty: ${missing.join(", ")}`);
  return normalized;
}

async function assertCoverAspect(packageDir, outputs) {
  if (!outputs.includes("cover/cover.png")) return;
  const file = resolve(packageDir, "cover/cover.png");
  const data = await readFile(file);
  if (data.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a" || data.toString("ascii", 12, 16) !== "IHDR") {
    throw new Error("cover/cover.png must be a readable PNG");
  }
  const width = data.readUInt32BE(16);
  const height = data.readUInt32BE(20);
  const ratio = width / height;
  if (Math.abs(ratio - 2.35) > 0.04) {
    throw new Error(`cover/cover.png must be 2.35:1; received ${width}x${height} (${ratio.toFixed(4)})`);
  }
}

function now() {
  return new Date().toISOString();
}

function stageRecord(manifest, stage) {
  const record = manifest.stages.find((item) => item.id === stage);
  if (!record) throw new Error(`Unknown stage: ${stage}`);
  return record;
}

function assertStageOrder(manifest, stage) {
  const index = STAGES.indexOf(stage);
  const unresolved = manifest.stages
    .filter((record) => STAGES.indexOf(record.id) < index && record.status !== "skipped" && record.status !== "completed")
    .map((record) => `${record.id}:${record.status}`);
  if (unresolved.length) throw new Error(`Cannot start ${stage}; earlier stages are not complete: ${unresolved.join(", ")}`);
}

function resolveIllustrationSkill(args) {
  const skill = args["illustration-skill"] || DEFAULT_ILLUSTRATION_SKILL;
  if (!ILLUSTRATION_SKILLS.has(skill)) {
    throw new Error(`Unknown illustration skill: ${skill}`);
  }
  return skill;
}

function baseStageRecords(executionStages, illustrationSkill) {
  return STAGES.map((id) => ({
    id,
    status: executionStages.includes(id) ? "pending" : "skipped",
    skill:
      id === "format" ? "baoyu-format-markdown" :
      id === "cover" ? "baoyu-cover-image" :
      id === "illustrate" ? illustrationSkill :
      id === "typeset" ? "gzh-design-skill" : null,
    invocation: null,
    inputs: [],
    outputs: [],
    checks: [],
    started_at: null,
    ended_at: null,
    error: null,
  }));
}

async function init(args) {
  const file = assertSafeAbsolutePath(required(args, "file"), "file");
  const packageDir = assertSafeAbsolutePath(required(args, "package-dir"), "package-dir");
  try {
    await stat(file);
    throw new Error(`Manifest already exists; refusing to overwrite: ${file}`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const manifestRelative = relative(packageDir, file);
  if (manifestRelative !== "execution-manifest.json") {
    throw new Error(`--file must be package_dir/execution-manifest.json: ${file}`);
  }
  const executionStages = list(required(args, "execution-stages"));
  const illustrationSkill = resolveIllustrationSkill(args);
  if (!executionStages.includes("prepare")) executionStages.unshift("prepare");
  for (const stage of executionStages) {
    if (!STAGES.includes(stage)) throw new Error(`Unknown execution stage: ${stage}`);
  }
  if (new Set(executionStages).size !== executionStages.length) throw new Error("Duplicate execution stage");
  const timestamp = now();
  const nativeDispatcherTool = args["native-dispatcher"] || "Skill";
  const requestedCoverText = coverTextOverride(args["cover-text-override"]);
  if (nativeDispatcherTool !== "Skill") {
    throw new Error(`Unsupported native dispatcher: ${nativeDispatcherTool}; expected Skill`);
  }
  const manifest = {
    manifest_version: "1.0",
    run_id: args["run-id"] || `wechat-${timestamp.replace(/[-:.TZ]/g, "")}-${randomUUID().slice(0, 8)}`,
    status: "pending",
    package_dir: packageDir,
    input: {
      kind: args["input-kind"] || (args.source ? "raw-markdown" : "package"),
      path: args["input-path"] || args.source || args["package-dir"],
    },
    source: args.source ? { path: args.source, ...(args["source-sha256"] ? { sha256: args["source-sha256"] } : {}) } : undefined,
    account: args.account || null,
    scope: {
      scope_source: args["scope-source"] || "default",
      prompt_evidence: args["prompt-evidence"] || null,
      requested_start_stage: args["start-stage"] || null,
      requested_end_stage: args["end-stage"] || null,
      requested_only_stage: args["only-stage"] || null,
      explicit_skips: list(args["explicit-skips"]),
      publish_requested: args["publish-requested"] !== "false",
      ...(requestedCoverText ? {
        cover_text_override: requestedCoverText,
      } : {}),
      execution_stages: executionStages,
      illustration_skill: illustrationSkill,
      ...(illustrationSkill === "jiyi-little-dancer-illustrations" ? {
        illustration_reference: args["illustration-reference"] || "illustrations/ip-reference.png",
      } : {}),
      native_dispatcher: {
        available: booleanArg(args["native-dispatcher-available"]),
        tool: nativeDispatcherTool,
        id: args["native-dispatcher-id"] || null,
      },
    },
    stages: baseStageRecords(executionStages, illustrationSkill),
    validation_report: null,
    publish_result: null,
    error: null,
    created_at: timestamp,
    updated_at: timestamp,
  };
  if (!manifest.source) delete manifest.source;
  await atomicWrite(file, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(JSON.stringify({ ok: true, command: "init", file, run_id: manifest.run_id }));
}

async function transition(args, command) {
  const file = assertSafeAbsolutePath(required(args, "file"), "file");
  const manifest = await readManifest(file);
  const stage = required(args, "stage");
  const record = stageRecord(manifest, stage);
  const timestamp = now();

  if (command === "stage-start") {
    if (record.status === "skipped") throw new Error(`Cannot start skipped stage: ${stage}`);
    if (!["pending", "failed", "blocked"].includes(record.status)) {
      throw new Error(`Cannot start ${stage} from status ${record.status}; create a new package or mark the current attempt failed`);
    }
    if (DOWNSTREAM_SKILLS.has(record.skill)) {
      if (!args["invocation-kind"]) {
        throw new Error(`Downstream stage ${stage} requires an explicit --invocation-kind`);
      }
      if (args["invocation-kind"] === "native-skill" && !args["invoked-skill"]) {
        throw new Error(`Native stage ${stage} requires --invoked-skill`);
      }
      if (args["invocation-kind"] === "native-skill" && args["invocation-source"] && args["invocation-source"] !== "runtime") {
        throw new Error(`Native stage ${stage} must use --invocation-source runtime`);
      }
      if (args["invocation-kind"] !== "native-skill") {
        throw new Error(`${stage} requires a native Skill invocation`);
      }
    }
    assertStageOrder(manifest, stage);
    const stageInputs = list(args.inputs);
    if (stage === "illustrate" && record.skill === "jiyi-little-dancer-illustrations") {
      const reference = manifest.scope?.illustration_reference || "illustrations/ip-reference.png";
      if (!stageInputs.includes(reference)) {
        throw new Error(`Illustrate stage requires the packaged IP reference input: ${reference}`);
      }
    }
    record.status = "running";
    record.started_at = timestamp;
    record.error = null;
    record.inputs = stageInputs;
    record.invocation = {
      kind: args["invocation-kind"] || (stage === "prepare" ? "manifest-bootstrap" : stage === "publish" ? "bundled-publisher" : "native-skill"),
      id: args["invocation-id"] || null,
      ...(args["invocation-kind"] === "native-skill" ? {
        tool: args["invocation-tool"] || manifest.scope?.native_dispatcher?.tool || "Skill",
        source: args["invocation-source"] || "runtime",
        skill: args["invoked-skill"] || record.skill,
      } : {}),
      ...(args.authorization ? { authorization: args.authorization } : {}),
    };
    manifest.status = "running";
  } else if (command === "stage-complete") {
    if (!record.started_at && stage !== "prepare") throw new Error(`Stage was not started: ${stage}`);
    if (stage !== "prepare" && record.status !== "running") throw new Error(`Stage ${stage} is not running`);
    if (stage === "prepare" && (!manifest.scope?.native_dispatcher?.available || manifest.scope?.native_dispatcher?.tool !== "Skill") && manifest.scope.execution_stages.some((id) => id !== "prepare" && id !== "validate" && id !== "publish")) {
      throw new Error("Cannot complete prepare without a native Skill dispatcher; mark prepare as blocked");
    }
    const declaredOutputs = list(args.outputs);
    if (stage !== "prepare" && declaredOutputs.length === 0) {
      throw new Error(`Stage ${stage} requires at least one declared output`);
    }
    const stageOutputs = await normalizeAndCheckOutputs(manifest.package_dir, declaredOutputs);
    await assertStageHandoff(manifest, stage, stageOutputs);
    await assertCoverAspect(manifest.package_dir, stageOutputs);
    if (stage === "cover") {
      await assertCoverTextPolicy(manifest.package_dir, stageOutputs, manifest);
      await assertCoverPromptValidation(manifest.package_dir, stageOutputs);
    }
    if (stage === "illustrate" && record.skill === "jiyi-little-dancer-illustrations") {
      const littleDancerOutputs = [
        "illustrations/outline.md",
        "illustrations/outline-validation.json",
        "illustrations/prompt-validation.json",
        "illustrations/image-validation.json",
        "article-illustrated.md",
      ];
      const requiredOutputs = [
        manifest.scope?.illustration_reference || "illustrations/ip-reference.png",
        ...littleDancerOutputs,
        "illustrations/illustration-handoff.json",
      ];
      const missing = requiredOutputs.filter((output) => !stageOutputs.includes(output));
      if (missing.length) throw new Error(`Illustrate handoff is incomplete; missing outputs: ${missing.join(", ")}`);
    } else if (stage === "illustrate" && record.skill === "baoyu-article-illustrator") {
      const requiredOutputs = ["illustrations/outline.md", "article-illustrated.md"];
      const missing = requiredOutputs.filter((output) => !stageOutputs.includes(output));
      if (missing.length) throw new Error(`Illustrate handoff is incomplete; missing outputs: ${missing.join(", ")}`);
    }
    record.status = "completed";
    record.ended_at = timestamp;
    const claimedChecks = jsonValue(args.checks, []);
    if (!Array.isArray(claimedChecks)) throw new Error("--checks must be a JSON array");
    if (claimedChecks.some((check) => check && check.passed === false)) {
      throw new Error("A failed check cannot be recorded as stage-complete; use stage-fail instead");
    }
    record.outputs = stageOutputs;
    record.checks = [
      ...claimedChecks,
      { name: "declared-outputs-exist", passed: true, detail: `${stageOutputs.length} output(s)` },
    ];
    if (stage === "validate" && args.report) manifest.validation_report = args.report;
    if (stage === "publish" && args.result) manifest.publish_result = args.result;
    const active = manifest.stages.filter((item) => item.status !== "skipped");
    if (active.every((item) => item.status === "completed")) manifest.status = "completed";
  } else if (command === "stage-fail") {
    const blocked = args.status === "blocked";
    record.status = blocked ? "blocked" : "failed";
    record.ended_at = timestamp;
    record.error = required(args, "error");
    manifest.status = record.status;
    manifest.error = record.error;
  }

  manifest.updated_at = timestamp;
  await atomicWrite(file, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(JSON.stringify({ ok: true, command, file, stage, status: record.status }));
}

async function recordCompletionFailure(args, message) {
  if (!args.file || !args.stage) return;
  try {
    const file = assertSafeAbsolutePath(args.file, "file");
    const manifest = await readManifest(file);
    const record = stageRecord(manifest, args.stage);
    if (record.status !== "running") return;
    const timestamp = now();
    record.status = "failed";
    record.ended_at = timestamp;
    record.error = message;
    manifest.status = "failed";
    manifest.error = `${args.stage}: ${message}`;
    manifest.updated_at = timestamp;
    await atomicWrite(file, `${JSON.stringify(manifest, null, 2)}\n`);
  } catch {
    // Preserve the original CLI error when the manifest itself cannot be updated.
  }
}

function validateManifest(manifest) {
  const errors = [];
  if (manifest.manifest_version !== "1.0") errors.push("manifest_version must be 1.0");
  if (!manifest.run_id) errors.push("run_id is required");
  if (!manifest.package_dir) errors.push("package_dir is required");
  if (!manifest.input?.kind || manifest.input.path === undefined) errors.push("input.kind and input.path are required");
  if (!manifest.scope || !Array.isArray(manifest.scope.execution_stages)) errors.push("scope.execution_stages is required");
  if (manifest.scope?.illustration_skill !== undefined && !ILLUSTRATION_SKILLS.has(manifest.scope.illustration_skill)) {
    errors.push(`unknown illustration skill: ${manifest.scope.illustration_skill}`);
  }
  if (manifest.scope?.illustration_skill === "jiyi-little-dancer-illustrations" && !manifest.scope.illustration_reference) {
    errors.push("jiyi-little-dancer-illustrations requires scope.illustration_reference");
  }
  if (manifest.scope?.cover_text_override && !COVER_TEXT_LEVELS.has(manifest.scope.cover_text_override)) {
    errors.push(`invalid cover_text_override: ${manifest.scope.cover_text_override}`);
  }
  if (!Array.isArray(manifest.stages)) errors.push("stages must be an array");
  const seen = new Set();
  for (const record of manifest.stages || []) {
    if (!STAGES.includes(record.id)) errors.push(`unknown stage: ${record.id}`);
    if (seen.has(record.id)) errors.push(`duplicate stage: ${record.id}`);
    seen.add(record.id);
    if (!["pending", "running", "completed", "skipped", "failed", "blocked"].includes(record.status)) {
      errors.push(`invalid status for ${record.id}: ${record.status}`);
    }
    if (record.status === "completed" && DOWNSTREAM_SKILLS.has(record.skill)) {
      const nativeInvocation = record.invocation?.kind === "native-skill"
        && record.invocation?.tool === "Skill"
        && record.invocation?.source === "runtime"
        && (record.invocation?.skill || record.skill) === record.skill;
      if (!nativeInvocation) {
        errors.push(`${record.id} is completed without a valid native-skill invocation`);
      }
    }
  }
  for (const stage of STAGES) {
    if (!seen.has(stage)) errors.push(`missing stage: ${stage}`);
  }
  const illustrationRecord = (manifest.stages || []).find((record) => record.id === "illustrate");
  if (manifest.scope?.illustration_skill && illustrationRecord?.skill !== manifest.scope.illustration_skill) {
    errors.push("scope.illustration_skill does not match illustrate.skill");
  }
  if (manifest.scope?.native_dispatcher?.available && manifest.scope.native_dispatcher.tool !== "Skill") {
    errors.push("native_dispatcher.available requires the Skill tool");
  }
  if (manifest.scope?.illustration_skill === "jiyi-little-dancer-illustrations" && illustrationRecord?.status === "completed") {
    const reference = manifest.scope.illustration_reference;
    if (!illustrationRecord.inputs?.includes(reference)) errors.push("illustrate inputs do not include scope.illustration_reference");
    if (!illustrationRecord.outputs?.includes(reference)) errors.push("illustrate outputs do not include scope.illustration_reference");
    if (!illustrationRecord.outputs?.includes("illustrations/illustration-handoff.json")) errors.push("illustrate outputs do not include illustration-handoff.json");
  }
  const activeDownstream = (manifest.scope?.execution_stages || []).some((id) => id !== "prepare" && id !== "validate" && id !== "publish");
  if (manifest.stages?.find((record) => record.id === "prepare")?.status === "completed" && activeDownstream && (!manifest.scope?.native_dispatcher?.available || manifest.scope?.native_dispatcher?.tool !== "Skill")) {
    errors.push("prepare is completed although no native Skill dispatcher was recorded");
  }
  const serialized = JSON.stringify(manifest);
  if (/(access[_-]?token|app[_-]?secret|client[_-]?secret|authorization\s*:\s*bearer|WECHAT_ACCESS_TOKEN|OPENAI_API_KEY)/i.test(serialized)) {
    errors.push("manifest appears to contain a credential or token");
  }
  return errors;
}

async function validate(args) {
  const file = assertSafeAbsolutePath(required(args, "file"), "file");
  const manifest = await readManifest(file);
  const errors = validateManifest(manifest);
  const result = { ok: errors.length === 0, file, errors };
  console.log(JSON.stringify(result, null, 2));
  if (errors.length) process.exitCode = 1;
}

function usage() {
  console.error(`Usage:\n  execution-manifest.mjs init --file FILE --package-dir DIR --execution-stages prepare,format,... [--illustration-skill baoyu-article-illustrator|jiyi-little-dancer-illustrations] [--illustration-reference illustrations/ip-reference.png] [--cover-text-override none|title-only|title-subtitle|text-rich] [--native-dispatcher Skill --native-dispatcher-available true]\n  execution-manifest.mjs stage-start --file FILE --stage ID [--inputs a,b] [--invocation-kind native-skill --invocation-source runtime --invoked-skill SKILL [--invocation-id RUNTIME_ID]]\n  execution-manifest.mjs stage-complete --file FILE --stage ID [--outputs a,b] [--checks JSON]\n  execution-manifest.mjs stage-fail --file FILE --stage ID --error MESSAGE [--status blocked]\n  execution-manifest.mjs validate --file FILE`);
}

const [command, ...rest] = process.argv.slice(2);
const args = parseArgs(rest);
try {
  if (command === "init") await init(args);
  else if (["stage-start", "stage-complete", "stage-fail"].includes(command)) await transition(args, command);
  else if (command === "validate") await validate(args);
  else {
    usage();
    process.exitCode = 2;
  }
} catch (error) {
  if (command === "stage-complete") await recordCompletionFailure(args, error.message);
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
