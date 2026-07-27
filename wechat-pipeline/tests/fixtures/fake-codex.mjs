#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const args = process.argv.slice(2);
const runtime = process.env.FAKE_AGENT_RUNTIME || "codex";
if (args[0] === "--version") {
  console.log(runtime === "claude" ? "2.1.217 (Claude Code)" : "codex-cli fake-1.0.0");
  process.exit(0);
}
if (args[0] === "exec" && args[1] === "--help") {
  console.log("--json --output-schema --output-last-message --ignore-user-config");
  process.exit(0);
}
if (args[0] === "--help") {
  console.log("--print --output-format --json-schema --resume --permission-mode");
  process.exit(0);
}
if (process.env.FAKE_CODEX_DELAY_MS) {
  await new Promise((done) => setTimeout(done, Number(process.env.FAKE_CODEX_DELAY_MS)));
}

const outputIndex = args.indexOf("-o");
const outputPath = outputIndex >= 0 ? args[outputIndex + 1] : null;
const prompt = readFileSync(0, "utf8");
const onePixelPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");

function write(path, content) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content);
  return path;
}

function parseRequest() {
  const match = prompt.match(/Stage 请求：\n([\s\S]+?)\n\n(?:执行模式：|此前 Agent 结果：)/);
  return match ? JSON.parse(match[1]) : null;
}

let result;
if (process.env.FAKE_CODEX_RESULT) {
  result = JSON.parse(process.env.FAKE_CODEX_RESULT);
} else {
  const request = parseRequest();
  if (!request) throw new Error("Fake Codex could not parse Stage request");
  if (request.stage === "format") {
    const analysis = write(resolve(request.output_dir, "source-analysis.md"), "# Analysis\n");
    const formatted = write(resolve(request.output_dir, "source-formatted.md"), "---\ntitle: 测试文章\nsummary: 测试摘要\n---\n\n正文\n");
    result = { status: "completed", summary: "formatted", artifacts: [
      { role: "analysis", path: analysis }, { role: "formatted_markdown", path: formatted },
    ], decisions: [{ name: "title", value: "测试文章" }], validation: [{ name: "format", status: "passed", details: "ok" }], questions: [] };
  } else if (request.stage === "cover") {
    const cover = write(resolve(request.output_dir, "cover.png"), onePixelPng);
    const promptFile = write(resolve(request.output_dir, "prompts/01-cover.md"), "cover prompt\n");
    result = { status: "completed", summary: "cover", artifacts: [
      { role: "cover_image", path: cover }, { role: "prompt", path: promptFile },
    ], decisions: [{ name: "type", value: "fake" }], validation: [{ name: "cover", status: "passed", details: "ok" }], questions: [] };
  } else if (request.stage === "illustrate") {
    const outline = write(resolve(request.output_dir, "outline.md"), "# Outline\n");
    const promptFile = write(resolve(request.output_dir, "prompts/01-scene.md"), "scene prompt\n");
    const image = write(resolve(request.output_dir, "01-scene.png"), onePixelPng);
    write(request.input_file, "---\ntitle: 测试文章\nsummary: 测试摘要\n---\n\n正文\n\n![配图](imgs/01-scene.png)\n");
    result = { status: "completed", summary: "illustrated", artifacts: [
      { role: "outline", path: outline }, { role: "prompt", path: promptFile },
      { role: "body_image", path: image }, { role: "illustrated_markdown", path: request.input_file },
    ], decisions: [{ name: "image_count", value: "1" }], validation: [{ name: "illustrate", status: "passed", details: "ok" }], questions: [] };
  } else if (request.stage === "layout") {
    const body = write(resolve(request.output_dir, "article.html"), '<section><span leaf="">正文</span><img src="imgs/01-scene.png"></section>');
    const preview = write(resolve(request.output_dir, "article-preview.html"), `<html><body>${readFileSync(body, "utf8")}</body></html>`);
    result = { status: "completed", summary: "layout", artifacts: [
      { role: "body_html", path: body }, { role: "preview_html", path: preview },
    ], decisions: [{ name: "theme", value: "fake" }], validation: [{ name: "layout", status: "passed", details: "ok" }], questions: [] };
  }
}

if (!result) throw new Error("Fake Codex has no result");
if (!result.execution_evidence) {
  const skillFile = prompt.match(/必须先完整读取 ([^\n]+?\/SKILL\.md)/)?.[1];
  if (!skillFile) throw new Error("Fake Agent could not locate the target SKILL.md");
  result.execution_evidence = {
    skill_file: {
      path: skillFile,
      sha256: createHash("sha256").update(readFileSync(skillFile)).digest("hex"),
    },
    references_read: [],
    scripts_executed: [],
  };
}
if (outputPath) write(outputPath, `${JSON.stringify(result)}\n`);
const request = parseRequest();
const continuation = prompt.includes("安全续接一个此前暂停的下游 Stage");
if (runtime === "claude") {
  const resumeIndex = args.indexOf("--resume");
  const sessionId = resumeIndex >= 0 ? args[resumeIndex + 1] : `fake-session-${request?.stage || "resume"}`;
  console.log(JSON.stringify({ type: "result", subtype: "success", session_id: sessionId,
    structured_output: result, usage: { input_tokens: 1, output_tokens: 1 } }));
} else {
  const threadId = `fake-thread-${request?.stage || "resume"}${continuation ? "-continuation" : ""}`;
  console.log(JSON.stringify({ type: "thread.started", thread_id: threadId }));
  console.log(JSON.stringify({ type: "item.completed", item: { type: "agent_message", text: JSON.stringify(result) } }));
  console.log(JSON.stringify({ type: "turn.completed", usage: { input_tokens: 1, output_tokens: 1 } }));
}
