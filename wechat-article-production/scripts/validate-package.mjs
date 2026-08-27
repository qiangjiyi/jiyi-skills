#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inspectWechatHtml } from "./wechat-html.mjs";
import { parseArgs, required, list, nonEmpty } from "./shared.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const STAGES = ["prepare", "format", "cover", "illustrate", "typeset", "validate", "publish"];
const REQUIRED_LITTLE_DANCER_ANCHORS = JSON.parse(
  readFileSync(join(__dirname, "../references/identity-anchors.json"), "utf8")
);

async function exists(file) {
  try {
    const info = await stat(file);
    return info.isFile();
  } catch {
    return false;
  }
}

async function sha256(file) {
  const content = await readFile(file);
  return createHash("sha256").update(content).digest("hex");
}

async function pngDimensions(file) {
  const data = await readFile(file);
  const signature = "89504e470d0a1a0a";
  if (data.subarray(0, 8).toString("hex") !== signature) return null;
  if (data.toString("ascii", 12, 16) !== "IHDR") return null;
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
}

function addCheck(checks, name, passed, detail = null) {
  checks.push({ name, passed, detail });
}

async function checkFile(checks, packageDir, relative) {
  const file = join(packageDir, relative);
  addCheck(checks, `file:${relative}`, await nonEmpty(file), `required non-empty artifact`);
  return file;
}

async function checkPromptDirectory(checks, packageDir, relative) {
  const directory = join(packageDir, relative);
  let promptFiles = [];
  try {
    promptFiles = (await readdir(directory, { withFileTypes: true }))
      .filter((entry) => entry.isFile() && /\.(?:md|txt)$/i.test(entry.name))
      .map((entry) => entry.name);
  } catch {
    // The check below reports the missing directory as a failed handoff.
  }
  addCheck(
    checks,
    `prompts:${relative}`,
    promptFiles.length > 0,
    promptFiles.length > 0 ? `${promptFiles.length} prompt file(s)` : "no prompt file found",
  );
}

async function checkCoverPromptAspect(checks, packageDir) {
  const directory = join(packageDir, "cover/prompts");
  let promptFiles = [];
  try {
    promptFiles = (await readdir(directory, { withFileTypes: true }))
      .filter((entry) => entry.isFile() && /\.(?:md|txt)$/i.test(entry.name))
      .map((entry) => entry.name);
  } catch {
    // The prompt-directory check reports the missing prompt directory.
  }
  if (promptFiles.length === 0) return;
  const contents = await Promise.all(promptFiles.map((file) => readFile(join(directory, file), "utf8")));
  const passed = contents.every((content) => /2[.．]35\s*[:：]\s*1/.test(content));
  addCheck(
    checks,
    "cover-prompt-aspect",
    passed,
    passed ? "all cover prompts declare 2.35:1" : "cover prompt must explicitly declare 2.35:1",
  );
}

async function checkCoverPromptValidation(checks, packageDir) {
  const file = join(packageDir, "cover/prompt-validation.json");
  if (!(await exists(file))) return;
  try {
    const report = JSON.parse(await readFile(file, "utf8"));
    addCheck(
      checks,
      "cover-prompt-validation",
      report.passed === true,
      report.passed === true ? "CoverImage prompt template and policy checks passed" : (report.errors || []).join("; ") || "cover prompt validation failed",
    );
  } catch (error) {
    addCheck(checks, "cover-prompt-validation", false, `invalid prompt validation report: ${error.message}`);
  }
}

async function checkFormatValidation(checks, packageDir) {
  const file = join(packageDir, "format-validation.json");
  if (!(await exists(file))) return;
  try {
    const report = JSON.parse(await readFile(file, "utf8"));
    addCheck(
      checks,
      "format-validation",
      report.passed === true,
      report.passed === true ? "native format output structure passed" : (report.errors || []).join("; ") || "format output validation failed",
    );
  } catch (error) {
    addCheck(checks, "format-validation", false, `invalid format validation report: ${error.message}`);
  }
}

async function checkIllustrationImages(checks, packageDir, stages) {
  if (!stages.includes("illustrate")) return;
  let imageFiles = [];
  try {
    imageFiles = (await readdir(join(packageDir, "illustrations"), { withFileTypes: true }))
      .filter((entry) => entry.isFile() && entry.name !== "ip-reference.png" && /\.(?:png|jpe?g|webp)$/i.test(entry.name))
      .map((entry) => entry.name);
  } catch {
    // The check below reports the missing illustration output.
  }
  addCheck(
    checks,
    "illustration-images",
    imageFiles.length > 0,
    imageFiles.length > 0 ? `${imageFiles.length} generated image file(s)` : "no generated illustration found",
  );
}

function requiredFiles(stages, manifest) {
  const files = new Set();
  if (stages.includes("format")) {
    files.add("source.md");
    files.add("analysis.md");
    files.add("formatted.md");
    files.add("format-validation.json");
  }
  if (stages.includes("cover") || stages.includes("publish")) files.add("cover/cover.png");
  if (stages.includes("cover")) {
    files.add("formatted.md");
    files.add("cover/prompt-validation.json");
  }
  if (stages.includes("illustrate")) {
    files.add("formatted.md");
    files.add("illustrations/outline.md");
    files.add("article-illustrated.md");
    if (manifest?.scope?.illustration_skill === "jiyi-little-dancer-illustrations") {
      files.add(manifest.scope.illustration_reference || "illustrations/ip-reference.png");
      files.add("illustrations/outline-validation.json");
      files.add("illustrations/prompt-validation.json");
      files.add("illustrations/image-validation.json");
      files.add("illustrations/illustration-handoff.json");
    }
  }
  if (stages.includes("typeset")) {
    const illustrationWasSelected = stages.includes("illustrate") || manifest?.scope?.execution_stages?.includes("illustrate");
    files.add(illustrationWasSelected ? "article-illustrated.md" : "formatted.md");
    files.add("article.html");
    files.add("article-preview.html");
  }
  if (stages.includes("publish")) files.add("article.html");
  return [...files];
}

async function checkMarkdownRefs(checks, packageDir, relative) {
  if (!(await exists(join(packageDir, relative)))) return;
  const content = await readFile(join(packageDir, relative), "utf8");
  const refs = [...content.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)].map((match) => match[1].trim());
  let allResolved = true;
  for (const ref of refs) {
    if (/^(?:https?:|data:|#)/i.test(ref)) continue;
    const target = resolve(dirname(join(packageDir, relative)), ref);
    if (!(await exists(target))) allResolved = false;
  }
  addCheck(checks, `markdown-images:${relative}`, allResolved, `${refs.length} image reference(s)`);
}

async function checkIllustrationHandoff(checks, packageDir, manifest, stages) {
  if (!stages.includes("illustrate") || manifest?.scope?.illustration_skill !== "jiyi-little-dancer-illustrations") return;
  const file = join(packageDir, "illustrations/illustration-handoff.json");
  if (!(await exists(file))) return;
  const errors = [];
  let handoff;
  try {
    handoff = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    addCheck(checks, "illustration-handoff", false, `invalid JSON: ${error.message}`);
    return;
  }
  if (handoff.source_file !== "formatted.md") errors.push("source_file must be formatted.md");
  if (handoff.article_file !== "article-illustrated.md") errors.push("article_file must be article-illustrated.md");
  if (handoff.illustration_skill !== manifest.scope.illustration_skill) errors.push("illustration_skill does not match manifest");
  if (!handoff.image_backend || typeof handoff.image_backend.skill !== "string" || !handoff.image_backend.skill.trim()) {
    errors.push("image_backend.skill must record the actual backend Skill");
  }
  if (!Array.isArray(handoff.image_backend?.per_image) || handoff.image_backend.per_image.length === 0) {
    errors.push("image_backend.per_image must be a non-empty array");
  } else {
    handoff.image_backend.per_image.forEach((entry, index) => {
      if (!entry || typeof entry !== "object") {
        errors.push(`image_backend.per_image[${index}] must be an object`);
        return;
      }
      if (typeof entry.skill !== "string" || !entry.skill.trim()) errors.push(`image_backend.per_image[${index}].skill must record the actual backend Skill`);
      if (!entry.prompt_file) errors.push(`image_backend.per_image[${index}].prompt_file is required`);
      if (!entry.output) errors.push(`image_backend.per_image[${index}].output is required`);
      if (entry.aspect !== "16:9") errors.push(`image_backend.per_image[${index}].aspect must be 16:9`);
      if (entry.reference_used_for_identity !== true) errors.push(`image_backend.per_image[${index}].reference_used_for_identity must be true`);
    });
  }
  if (handoff.prompt_validation !== "illustrations/prompt-validation.json") {
    errors.push("prompt_validation must reference illustrations/prompt-validation.json");
  } else {
    try {
      const promptReport = JSON.parse(await readFile(resolve(packageDir, handoff.prompt_validation), "utf8"));
      if (promptReport.passed !== true) errors.push("prompt-validation report must be passed");
    } catch (error) {
      errors.push(`invalid prompt-validation report: ${error.message}`);
    }
  }
  if (handoff.outline_validation !== "illustrations/outline-validation.json") {
    errors.push("outline_validation must reference illustrations/outline-validation.json");
  } else {
    try {
      const outlineReport = JSON.parse(await readFile(resolve(packageDir, handoff.outline_validation), "utf8"));
      if (outlineReport.passed !== true) errors.push("outline-validation report must be passed");
    } catch (error) {
      errors.push(`invalid outline-validation report: ${error.message}`);
    }
  }
  if (handoff.image_validation !== "illustrations/image-validation.json") {
    errors.push("image_validation must reference illustrations/image-validation.json");
  } else {
    try {
      const imageReport = JSON.parse(await readFile(resolve(packageDir, handoff.image_validation), "utf8"));
      if (imageReport.passed !== true) errors.push("image-validation report must be passed");
    } catch (error) {
      errors.push(`invalid image-validation report: ${error.message}`);
    }
  }
  const expectedReference = manifest.scope.illustration_reference || "illustrations/ip-reference.png";
  if (!handoff.reference_asset || typeof handoff.reference_asset !== "object") {
    errors.push("reference_asset is required");
  } else {
    if (handoff.reference_asset.package_path !== expectedReference) errors.push("reference_asset.package_path does not match manifest");
    if (handoff.reference_asset.used_for_identity !== true) errors.push("reference_asset.used_for_identity must be true");
    if (!handoff.reference_asset.source) errors.push("reference_asset.source is required");
    if (!(await exists(resolve(packageDir, handoff.reference_asset.package_path || "")))) errors.push(`missing reference asset: ${handoff.reference_asset.package_path || "(empty)"}`);
  }
  if (!Array.isArray(handoff.identity_anchors) || handoff.identity_anchors.length < 4) {
    errors.push("identity_anchors must list at least four fixed character anchors");
  } else {
    const anchorText = handoff.identity_anchors.join("、");
    const missingAnchors = REQUIRED_LITTLE_DANCER_ANCHORS.filter((anchor) => !anchorText.includes(anchor));
    if (missingAnchors.length) errors.push(`missing fixed identity anchors: ${missingAnchors.join(", ")}`);
  }
  if (!Array.isArray(handoff.illustrations) || handoff.count !== handoff.illustrations.length) {
    errors.push("count must equal illustrations.length");
  }
  const article = await readFile(join(packageDir, "article-illustrated.md"), "utf8").catch(() => "");
  for (const item of handoff.illustrations || []) {
    if (!item.relative_path || !(await exists(resolve(packageDir, item.relative_path)))) errors.push(`missing image: ${item.relative_path || "(empty)"}`);
    if (!item.prompt_file || !(await exists(resolve(packageDir, item.prompt_file)))) errors.push(`missing prompt: ${item.prompt_file || "(empty)"}`);
    if (!item.alt || !item.anchor) errors.push(`image ${item.index || "?"} must have alt and anchor`);
    if (item.relative_path && !article.includes(`](${item.relative_path})`)) errors.push(`article missing image reference: ${item.relative_path}`);
  }
  addCheck(checks, "illustration-handoff", errors.length === 0, errors.length ? errors.join("; ") : `${handoff.count} image placement(s) resolved`);
}

async function checkHtml(checks, packageDir) {
  const file = join(packageDir, "article.html");
  if (!(await exists(file))) return;
  const content = await readFile(file, "utf8");
  const inspection = inspectWechatHtml(content);
  addCheck(checks, "html-wechat-compatibility", inspection.ok, inspection.errors.length ? inspection.errors.join("; ") : "WeChat body-fragment and platform rules");
  addCheck(checks, "html-body-fragment", !/<\/?(?:!doctype|html|head|body)\b/i.test(content), "clean HTML must be a body fragment");
  addCheck(checks, "html-images-syntax", !inspection.errors.some((error) => /<img>|img|image/i.test(error)), "all <img> tags have parseable ASCII-quoted src attributes");
  const refs = inspection.sources;
  let allResolved = true;
  for (const ref of refs) {
    if (/^(?:https?:|data:)/i.test(ref)) continue;
    if (!(await exists(resolve(packageDir, ref)))) allResolved = false;
  }
  addCheck(checks, "html-images-resolve", allResolved, `${refs.length} image source(s)`);
  addCheck(checks, "no-placeholders", !/(\{\{[^}]+\}\}|待补|插入)/.test(content), "HTML placeholder scan");
}

function nativeSkillAliases(skill) {
  return skill === "gzh-design" ? new Set(["gzh-design", "gzh-design-skill"]) : new Set([skill]);
}

async function findNativeSkillCalls(packageDir, manifest) {
  const projectRoot = resolve(homedir(), ".claude", "projects");
  const candidates = [];
  let projectEntries;
  try {
    projectEntries = await readdir(projectRoot, { withFileTypes: true });
  } catch {
    return { file: null, calls: [] };
  }
  for (const projectEntry of projectEntries) {
    if (!projectEntry.isDirectory()) continue;
    const projectDir = join(projectRoot, projectEntry.name);
    let entries;
    try {
      entries = await readdir(projectDir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith(".jsonl")) continue;
      const file = join(projectDir, entry.name);
      let content;
      let info;
      try {
        info = await stat(file);
        if (info.mtimeMs < Date.parse(manifest.created_at || "") - 5_000) continue;
        content = await readFile(file, "utf8");
      } catch {
        continue;
      }
      if (!content.includes(packageDir) || !content.includes('"name":"Skill"')) continue;
      const calls = [];
      for (const line of content.split(/\r?\n/)) {
        let record;
        try {
          record = JSON.parse(line);
        } catch {
          continue;
        }
        const message = record?.message;
        if (!message || !Array.isArray(message.content)) continue;
        for (const item of message.content) {
          if (item?.type !== "tool_use" || item.name !== "Skill") continue;
          if (typeof item.input?.skill !== "string") continue;
          calls.push({
            id: item.id || null,
            skill: item.input.skill,
            timestamp: record.timestamp || null,
            args: typeof item.input.args === "string" ? item.input.args : "",
          });
        }
      }
      if (calls.length) candidates.push({ file, mtimeMs: info.mtimeMs, calls });
    }
  }
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return {
    file: candidates.map((candidate) => candidate.file).join(", "),
    calls: candidates.flatMap((candidate) => candidate.calls),
  };
}

async function checkNativeSkillTranscript(checks, packageDir, manifest, stages) {
  if (!manifest || !Array.isArray(manifest.stages)) return;
  const required = manifest.stages.filter((record) =>
    stages.includes(record.id) && record.status === "completed" && record.invocation?.kind === "native-skill",
  );
  if (!required.length) return;
  const transcript = await findNativeSkillCalls(packageDir, manifest);
  const missing = [];
  const evidence = [];
  for (const record of required) {
    const allowed = nativeSkillAliases(record.skill);
    const started = Date.parse(record.started_at || "") || -Infinity;
    const ended = Date.parse(record.ended_at || "") || Infinity;
    const matches = transcript.calls.filter((call) => {
      if (!allowed.has(call.skill)) return false;
      const timestamp = Date.parse(call.timestamp || "") || 0;
      const inStageWindow = timestamp >= started - 1_000 && timestamp <= ended + 1_000;
      const packageMentioned = call.args.includes(packageDir);
      return inStageWindow || packageMentioned;
    });
    evidence.push(`${record.id}=${matches.length}`);
    if (!matches.length) missing.push(`${record.id} (${record.skill})`);
  }
  const detail = `${transcript.file || "no matching Claude Code transcript"}; ${evidence.join(", ")}`;
  addCheck(checks, "native-skill-transcript", missing.length === 0, missing.length ? `missing real Skill tool-use: ${missing.join(", ")}; ${detail}` : detail);

}

async function checkRatio(checks, packageDir, relative, expected, tolerance) {
  const file = join(packageDir, relative);
  if (!(await exists(file))) return;
  const dimensions = await pngDimensions(file);
  if (!dimensions) {
    addCheck(checks, `ratio:${relative}`, false, "expected a readable PNG");
    return;
  }
  const ratio = dimensions.width / dimensions.height;
  addCheck(checks, `ratio:${relative}`, Math.abs(ratio - expected) <= tolerance, `${dimensions.width}x${dimensions.height} = ${ratio.toFixed(4)}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const packageDir = resolve(required(args, "package"));
  let manifest = null;
  if (args.manifest) manifest = JSON.parse(await readFile(resolve(args.manifest), "utf8"));
  let stages = list(args.stages).length ? list(args.stages) : manifest?.scope?.execution_stages || STAGES;
  if (stages.length === 1 && stages[0] === "validate") {
    stages = ["format", "cover", "illustrate", "typeset", "validate"];
  }
  for (const stage of stages) {
    if (!STAGES.includes(stage)) throw new Error(`Unknown stage: ${stage}`);
  }
  const checks = [];

  for (const relative of requiredFiles(stages, manifest)) await checkFile(checks, packageDir, relative);
  if (stages.includes("cover")) {
    await checkPromptDirectory(checks, packageDir, "cover/prompts");
    await checkCoverPromptAspect(checks, packageDir);
    await checkCoverPromptValidation(checks, packageDir);
  }
  if (stages.includes("format")) await checkFormatValidation(checks, packageDir);
  if (stages.includes("illustrate")) await checkPromptDirectory(checks, packageDir, "illustrations/prompts");
  await checkIllustrationImages(checks, packageDir, stages);
  if (stages.includes("cover") || stages.includes("publish")) {
    await checkRatio(checks, packageDir, "cover/cover.png", 2.35, 0.04);
  }
  if (stages.includes("illustrate") || stages.includes("typeset") || stages.includes("validate") || stages.includes("publish")) {
    const imageFiles = [];
    if (manifest?.stages) {
      const illustration = manifest.stages.find((stage) => stage.id === "illustrate");
      for (const output of illustration?.outputs || []) {
        if (/\.(?:png|jpe?g|webp)$/i.test(output)) imageFiles.push(output);
      }
    }
    if (imageFiles.length === 0) {
      try {
        const entries = await readdir(join(packageDir, "illustrations"));
        for (const entry of entries) {
          if (/\.(?:png|jpe?g|webp)$/i.test(entry)) imageFiles.push(join("illustrations", entry));
        }
      } catch {
        // The required artifact checks below report a missing illustration handoff when needed.
      }
    }
    for (const relative of imageFiles) await checkRatio(checks, packageDir, relative, 16 / 9, 0.04);
  }
  if (stages.includes("format") || stages.includes("cover") || stages.includes("illustrate") || stages.includes("typeset")) {
    const illustrationWasSelected = stages.includes("illustrate") || (stages.includes("typeset") && manifest?.scope?.execution_stages?.includes("illustrate"));
    const markdownHandoff = illustrationWasSelected
      ? "article-illustrated.md"
      : "formatted.md";
    await checkMarkdownRefs(checks, packageDir, markdownHandoff);
  }
  await checkIllustrationHandoff(checks, packageDir, manifest, stages);
  if (stages.includes("typeset") || stages.includes("publish")) await checkHtml(checks, packageDir);
  await checkNativeSkillTranscript(checks, packageDir, manifest, stages);

  if (manifest?.source?.sha256 && manifest.source.path) {
    const sourceMatches = (await sha256(resolve(manifest.source.path))) === manifest.source.sha256;
    addCheck(checks, "source-integrity", sourceMatches, manifest.source.path);
  }
  if (manifest?.validation_report && stages.includes("validate")) {
    addCheck(checks, "manifest-validation-report", await exists(resolve(packageDir, manifest.validation_report)), manifest.validation_report);
  }

  const failed = checks.filter((check) => !check.passed);
  const report = {
    report_version: "1.0",
    package_dir: packageDir,
    stages,
    ok: failed.length === 0,
    checks,
    generated_at: new Date().toISOString(),
  };
  const reportFile = resolve(args["report-file"] || join(packageDir, "validation-report.json"));
  await mkdir(dirname(reportFile), { recursive: true });
  await writeFile(reportFile, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exitCode = 1;
}

try {
  await main();
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
