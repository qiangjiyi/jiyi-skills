#!/usr/bin/env node

import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

const REQUIRED_SECTIONS = [
  ["Visual DNA", /^(?:#{1,3}\s*)?(?:Visual DNA|视觉\s*DNA|视觉风格)\b/i],
  ["IP identity lock", /^(?:#{1,3}\s*)?(?:IP identity lock|IP\s*身份|身份锚点|角色身份)\b/i],
  ["Primary movement", /^(?:#{1,3}\s*)?(?:Primary movement|Movement|主动作|动作与身体语言)\b/i],
  ["Expression", /^(?:#{1,3}\s*)?(?:Expression|主表情|表情)\b/i],
  ["Gaze direction", /^(?:#{1,3}\s*)?(?:Gaze direction|视线方向|视线)\b/i],
  ["Theme", /^(?:#{1,3}\s*)?(?:Theme|主题)\b/i],
  ["Structure type", /^(?:#{1,3}\s*)?(?:Structure type|结构类型)\b/i],
  ["Core idea", /^(?:#{1,3}\s*)?(?:Core idea|核心意思|核心含义)\b/i],
  ["Composition", /^(?:#{1,3}\s*)?(?:Composition|构图)\b/i],
  ["Suggested elements", /^(?:#{1,3}\s*)?(?:Suggested elements|建议元素|画面元素)\b/i],
  ["Chinese handwritten labels", /^(?:#{1,3}\s*)?(?:Chinese handwritten labels|中文手写批注|中文标注|标注词)\b/i],
  ["Color use", /^(?:#{1,3}\s*)?(?:Color use|颜色使用|配色)\b/i],
  ["Constraints", /^(?:#{1,3}\s*)?(?:Constraints|约束|限制条件)\b/i],
  ["Reference handling", /^(?:#{1,3}\s*)?(?:Reference handling|参考图处理|视觉参考)\b/i],
];

const REQUIRED_CONTENT = [
  ["aspect ratio", /16\s*:\s*9/],
  ["white background", /纯白|Pure white/i],
  ["hand-drawn line art", /手绘|hand[- ]drawn/i],
  ["little dancer identity", /小舞伴|little dancer|little girl/i],
  ["single core structure", /一个核心|one core|only one core/i],
  ["anti-PPT constraint", /PPT|正式流程图|course slide/i],
];

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      args[key] = next;
      i += 1;
    } else {
      args[key] = true;
    }
  }
  return args;
}

async function exists(file) {
  try {
    const info = await stat(file);
    return info.isFile();
  } catch {
    return false;
  }
}

function hasSection(lines, pattern) {
  return lines.some((line) => pattern.test(line.trim()));
}

function validatePrompt(content, file) {
  const lines = content.split(/\r?\n/);
  const errors = [];

  for (const [name, pattern] of REQUIRED_SECTIONS) {
    if (!hasSection(lines, pattern)) errors.push(`missing section: ${name}`);
  }
  for (const [name, pattern] of REQUIRED_CONTENT) {
    if (!pattern.test(content)) errors.push(`missing constraint: ${name}`);
  }
  if (/\{[^}\n]+\}/.test(content)) errors.push("unresolved template placeholder");

  return { file, passed: errors.length === 0, line_count: lines.length, errors };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args["prompt-dir"]) throw new Error("Missing --prompt-dir");

  const promptDir = resolve(args["prompt-dir"]);
  const entries = await readdir(promptDir, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && /\.(?:md|txt)$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  if (files.length === 0) throw new Error(`No prompt files found in ${promptDir}`);

  const results = [];
  for (const file of files) {
    const path = join(promptDir, file);
    results.push(validatePrompt(await readFile(path, "utf8"), path));
  }

  if (args.reference) {
    const reference = resolve(args.reference);
    if (!(await exists(reference))) {
      results.push({ file: reference, passed: false, line_count: 0, errors: ["reference image does not exist"] });
    }
  }

  const failed = results.filter((result) => !result.passed);
  const report = {
    schema_version: "1.0",
    prompt_dir: promptDir,
    prompt_count: files.length,
    passed: failed.length === 0,
    results,
  };

  if (args["report-file"]) {
    const reportFile = resolve(args["report-file"]);
    await mkdir(dirname(reportFile), { recursive: true });
    await writeFile(reportFile, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }

  if (args.json) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    console.log(`Prompt validation: ${report.passed ? "PASS" : "FAIL"} (${files.length} prompt file(s))`);
    for (const result of results) {
      console.log(`${result.passed ? "✓" : "✗"} ${result.file}`);
      for (const error of result.errors) console.log(`  - ${error}`);
    }
  }
  process.exitCode = report.passed ? 0 : 1;
}

main().catch((error) => {
  console.error(`Prompt validation failed: ${error.message}`);
  process.exitCode = 1;
});
