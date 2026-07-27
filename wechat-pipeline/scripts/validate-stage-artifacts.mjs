#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { isWithin, loadAndValidateRequest, readableFile, STAGES } from "./stage-common.mjs";

function roleMap(artifacts) {
  const map = new Map();
  for (const artifact of artifacts || []) {
    if (!map.has(artifact.role)) map.set(artifact.role, []);
    map.get(artifact.role).push(resolve(artifact.path));
  }
  return map;
}

async function validateImage(path) {
  const data = await readFile(path);
  const png = data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpg = data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff;
  const gif = ["GIF87a", "GIF89a"].includes(data.subarray(0, 6).toString("ascii"));
  const webp = data.length >= 12 && data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP";
  if (!png && !jpg && !gif && !webp) throw new Error(`Invalid raster image: ${path}`);
}

async function validateMarkdownImages(path) {
  const text = await readFile(path, "utf8");
  const local = [...text.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)]
    .map((match) => match[1].trim().replace(/^<|>$/g, ""))
    .filter((source) => source && !/^(?:https?:|data:)/i.test(source));
  for (const source of local) {
    const image = resolve(dirname(path), source.split(/[?#]/, 1)[0]);
    if (!(await readableFile(image))) throw new Error(`Markdown image is unreadable: ${image}`);
    await validateImage(image);
  }
  return local.length;
}

async function validatePromptArtifact(path) {
  const metadata = await stat(path);
  if (metadata.isFile()) {
    if (metadata.size === 0) throw new Error(`Prompt artifact is empty: ${path}`);
    return 1;
  }
  if (!metadata.isDirectory()) throw new Error(`Prompt artifact is neither a file nor directory: ${path}`);
  const entries = await readdir(path, { withFileTypes: true });
  const promptFiles = entries.filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".md"));
  if (!promptFiles.length) throw new Error(`Prompt directory contains no Markdown files: ${path}`);
  for (const entry of promptFiles) {
    const file = resolve(path, entry.name);
    if ((await stat(file)).size === 0) throw new Error(`Prompt artifact is empty: ${file}`);
  }
  return promptFiles.length;
}

async function validateExecutionEvidence(skill, evidence) {
  const errors = [];
  const checks = [];
  if (!skill) return { checks, errors };
  if (!evidence?.skill_file) {
    return { checks, errors: ["Missing execution evidence for SKILL.md"] };
  }
  const expectedSkill = resolve(skill.file);
  if (resolve(evidence.skill_file.path) !== expectedSkill) errors.push("Execution evidence points to a different SKILL.md");
  try {
    const actual = await readFile(expectedSkill);
    const hash = createHash("sha256").update(actual).digest("hex");
    if (evidence.skill_file.sha256 !== hash || skill.sha256 !== hash) errors.push("SKILL.md evidence hash does not match");
  } catch {
    errors.push(`Execution evidence SKILL.md is unreadable: ${expectedSkill}`);
  }
  for (const group of ["references_read", "scripts_executed"]) {
    for (const item of evidence[group] || []) {
      const path = resolve(item.path);
      if (!isWithin(dirname(expectedSkill), path)) {
        errors.push(`Execution evidence is outside the Skill: ${path}`);
        continue;
      }
      try {
        const actual = await readFile(path);
        const hash = createHash("sha256").update(actual).digest("hex");
        if (item.sha256 !== hash) errors.push(`Execution evidence hash does not match: ${path}`);
      } catch {
        errors.push(`Execution evidence file is unreadable: ${path}`);
      }
      if (group === "scripts_executed" && item.exit_code !== 0) errors.push(`Skill script failed with exit ${item.exit_code}: ${path}`);
    }
  }
  checks.push("skill-entry-and-resource-evidence");
  return { checks, errors };
}

export async function validateStageArtifacts(request, agentResult, skill = null) {
  const checks = [];
  const errors = [];
  const roles = roleMap(agentResult.artifacts);
  const expected = STAGES[request.stage].roles;
  for (const role of expected) {
    if (!roles.get(role)?.length) errors.push(`Missing artifact role: ${role}`);
  }

  for (const artifact of agentResult.artifacts || []) {
    const path = resolve(artifact.path);
    const permittedInput = request.stage === "illustrate" && artifact.role === "illustrated_markdown" && path === request.input_file;
    if (!permittedInput && !isWithin(request.output_dir, path)) {
      errors.push(`Artifact is outside output scope: ${path}`);
      continue;
    }
    let st;
    try {
      st = await stat(path);
    } catch {
      errors.push(`Artifact is unreadable: ${path}`);
      continue;
    }
    if (artifact.role === "prompt") {
      try {
        await validatePromptArtifact(path);
      } catch (error) {
        errors.push(error.message);
      }
    } else if (!st.isFile()) {
      errors.push(`Artifact must be a file: ${path}`);
    } else if (st.size === 0) {
      errors.push(`Artifact is empty: ${path}`);
    }
  }

  try {
    if (request.stage === "format") {
      const formatted = roles.get("formatted_markdown")?.[0];
      if (formatted) {
        const text = await readFile(formatted, "utf8");
        if (!/^---\s*[\s\S]*?^---/m.test(text)) throw new Error("Formatted Markdown has no frontmatter");
        if (!/^title:\s*.+/m.test(text) || !/^summary:\s*.+/m.test(text)) throw new Error("Formatted Markdown lacks title or summary");
      }
      checks.push("formatted-markdown-metadata");
    } else if (request.stage === "cover") {
      for (const image of roles.get("cover_image") || []) await validateImage(image);
      checks.push("cover-raster-signature", "prompt-files-or-directory");
    } else if (request.stage === "illustrate") {
      for (const image of roles.get("body_image") || []) await validateImage(image);
      const article = roles.get("illustrated_markdown")?.[0];
      const count = article ? await validateMarkdownImages(article) : 0;
      if (count < (roles.get("body_image")?.length || 0)) throw new Error("Not all body images are referenced by illustrated Markdown");
      checks.push("body-images-and-markdown-references", "prompt-files-or-directory");
    } else if (request.stage === "layout") {
      const body = roles.get("body_html")?.[0];
      if (body) {
        const html = await readFile(body, "utf8");
        if (!/^\s*<section\b/i.test(html)) throw new Error("Body HTML must start with a section fragment");
        if (/<!doctype\b|<\/?(?:html|head|body)\b/i.test(html)) throw new Error("Body HTML contains a forbidden document wrapper");
        if (request.constraints?.no_unresolved_placeholders
          && /\{\{[^}]+\}\}|【[^】]*(?:待补|插入)[^】]*】/.test(html)) {
          throw new Error("Body HTML contains unresolved placeholders");
        }
        for (const match of html.matchAll(/<img\b[^>]*\bsrc=["']([^"']+)["']/gi)) {
          if (/^(?:https?:|data:)/i.test(match[1])) continue;
          const image = resolve(dirname(body), match[1].split(/[?#]/, 1)[0]);
          if (!(await readableFile(image))) throw new Error(`HTML image is unreadable: ${image}`);
        }
      }
      checks.push("wechat-html-fragment-and-local-images");
    }
  } catch (error) {
    errors.push(error.message);
  }

  if ((agentResult.validation || []).some((item) => item.status === "failed")) errors.push("Downstream Skill reported failed validation");
  const evidence = await validateExecutionEvidence(skill, agentResult.execution_evidence);
  checks.push(...evidence.checks);
  errors.push(...evidence.errors);
  return { passed: errors.length === 0, checks, errors };
}

async function main() {
  const args = process.argv.slice(2);
  const requestPath = args[args.indexOf("--request") + 1];
  const resultPath = args[args.indexOf("--agent-result") + 1];
  if (!requestPath || !resultPath) throw new Error("Usage: validate-stage-artifacts.mjs --request <request.json> --agent-result <result.json>");
  const request = await loadAndValidateRequest(requestPath);
  const result = JSON.parse(await readFile(resolve(resultPath), "utf8"));
  const validation = await validateStageArtifacts(request, result);
  console.log(JSON.stringify(validation, null, 2));
  if (!validation.passed) process.exitCode = 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
