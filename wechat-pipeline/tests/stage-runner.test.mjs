import assert from "node:assert/strict";
import { chmod, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const projectRoot = resolve(import.meta.dirname, "..");
const runner = resolve(projectRoot, "scripts/stage-runner.mjs");
const pipeline = resolve(projectRoot, "scripts/run-pipeline.mjs");
const fakeCodex = resolve(projectRoot, "tests/fixtures/fake-codex.mjs");

async function writeSkill(root, name, version = "1.0.0") {
  const dir = resolve(root, name);
  await mkdir(dir, { recursive: true });
  await writeFile(resolve(dir, "SKILL.md"), `---\nname: ${name}\ndescription: test\nversion: ${version}\n---\n\n# Test\n`, "utf8");
}

test.before(async () => chmod(fakeCodex, 0o755));

test("Stage Runner creates a provenance receipt after independent Agent completion", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-complete-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    await writeSkill(skills, "baoyu-format-markdown", "9.8.7");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "source.md");
    const analysis = resolve(runDir, "source-analysis.md");
    const formatted = resolve(runDir, "source-formatted.md");
    await writeFile(input, "正文\n", "utf8");
    await writeFile(analysis, "# Analysis\n", "utf8");
    await writeFile(formatted, "---\ntitle: 标题\nsummary: 摘要\n---\n\n正文\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "format", skill: "baoyu-format-markdown", run_dir: runDir,
      input_file: input, output_dir: runDir, mode: "skill-default", constraints: {}, user_preferences: {} }), "utf8");
    const agentResult = { status: "completed", summary: "ok", artifacts: [
      { role: "analysis", path: analysis }, { role: "formatted_markdown", path: formatted },
    ], decisions: [{ name: "title", value: "标题" }], validation: [{ name: "native", status: "passed", details: "ok" }], questions: [] };
    const result = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex, "--skill-root", skills],
      { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(agentResult) } });
    assert.equal(result.status, 0, result.stderr);
    const receipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/format/receipt.json"), "utf8"));
    assert.equal(receipt.status, "completed");
    assert.equal(receipt.skill.version, "9.8.7");
    assert.match(receipt.skill.sha256, /^[a-f0-9]{64}$/);
    assert.equal(receipt.agent.thread_id, "fake-thread-format");
    assert.equal(receipt.artifact_validation.passed, true);
    assert.equal(receipt.invocations.length, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner uses the Claude Code driver with the same evidence contract", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-claude-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    await writeSkill(skills, "baoyu-format-markdown", "2.0.0");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "source.md");
    const analysis = resolve(runDir, "source-analysis.md");
    const formatted = resolve(runDir, "source-formatted.md");
    await writeFile(input, "正文\n", "utf8");
    await writeFile(analysis, "# Analysis\n", "utf8");
    await writeFile(formatted, "---\ntitle: 标题\nsummary: 摘要\n---\n\n正文\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "format", skill: "baoyu-format-markdown", run_dir: runDir,
      input_file: input, output_dir: runDir, mode: "skill-default", constraints: {}, user_preferences: {} }), "utf8");
    const agentResult = { status: "completed", summary: "ok", artifacts: [
      { role: "analysis", path: analysis }, { role: "formatted_markdown", path: formatted },
    ], decisions: [], validation: [{ name: "native", status: "passed", details: "ok" }], questions: [] };
    const result = spawnSync(process.execPath, [runner, "--request", request, "--runtime", "claude",
      "--agent-bin", fakeCodex, "--skill-root", skills], {
      encoding: "utf8",
      env: { ...process.env, FAKE_AGENT_RUNTIME: "claude", FAKE_CODEX_RESULT: JSON.stringify(agentResult) },
    });
    assert.equal(result.status, 0, result.stderr);
    const receipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/format/receipt.json"), "utf8"));
    assert.equal(receipt.status, "completed");
    assert.equal(receipt.runtime.driver, "claude-print");
    assert.equal(receipt.invocations[0].runtime, "claude");
    assert.equal(receipt.agent.thread_id, "fake-session-format");
    assert.equal(receipt.artifact_validation.passed, true);
    assert.ok(receipt.artifact_validation.checks.includes("skill-entry-and-resource-evidence"));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner rejects a completed result with forged Skill execution evidence", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-forged-evidence-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    await writeSkill(skills, "baoyu-format-markdown");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "source.md");
    const analysis = resolve(runDir, "source-analysis.md");
    const formatted = resolve(runDir, "source-formatted.md");
    await writeFile(input, "正文\n", "utf8");
    await writeFile(analysis, "# Analysis\n", "utf8");
    await writeFile(formatted, "---\ntitle: 标题\nsummary: 摘要\n---\n\n正文\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "format", skill: "baoyu-format-markdown", run_dir: runDir,
      input_file: input, output_dir: runDir, mode: "skill-default", constraints: {}, user_preferences: {} }), "utf8");
    const agentResult = { status: "completed", summary: "not trustworthy", artifacts: [
      { role: "analysis", path: analysis }, { role: "formatted_markdown", path: formatted },
    ], decisions: [], validation: [{ name: "native", status: "passed", details: "ok" }], questions: [],
    execution_evidence: {
      skill_file: { path: resolve(skills, "baoyu-format-markdown/SKILL.md"), sha256: "0".repeat(64) },
      references_read: [],
      scripts_executed: [],
    } };
    const result = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex, "--skill-root", skills],
      { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(agentResult) } });
    assert.equal(result.status, 1, result.stderr);
    const receipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/format/receipt.json"), "utf8"));
    assert.equal(receipt.status, "failed");
    assert.equal(receipt.artifact_validation.passed, false);
    assert.ok(receipt.artifact_validation.errors.includes("SKILL.md evidence hash does not match"));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner preserves needs_input instead of selecting for the downstream Skill", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-input-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    await writeSkill(skills, "baoyu-cover-image");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "article.md");
    await writeFile(input, "---\ntitle: x\nsummary: y\n---\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "cover", skill: "baoyu-cover-image", run_dir: runDir,
      input_file: input, output_dir: resolve(runDir, "cover"), mode: "confirm", constraints: { aspect_ratio: "2.35:1" }, user_preferences: {} }), "utf8");
    const agentResult = { status: "needs_input", summary: "confirm", artifacts: [], decisions: [], validation: [],
      questions: [{ id: "cover_options", question: "确认推荐封面方案？" }] };
    const result = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex, "--skill-root", skills],
      { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(agentResult) } });
    assert.equal(result.status, 3, result.stderr);
    const receipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/cover/receipt.json"), "utf8"));
    assert.equal(receipt.status, "needs_input");
    assert.equal(receipt.agent.result.questions[0].id, "cover_options");
    assert.equal(receipt.artifact_validation.passed, false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner times out a stuck Agent and records the failure", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-timeout-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    await writeSkill(skills, "baoyu-format-markdown");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "source.md");
    await writeFile(input, "正文\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "format", skill: "baoyu-format-markdown", run_dir: runDir,
      input_file: input, output_dir: runDir, mode: "skill-default", constraints: {}, user_preferences: {} }), "utf8");
    const result = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex,
      "--skill-root", skills, "--no-default-skill-roots", "--timeout-ms", "50"],
    { encoding: "utf8", env: { ...process.env, FAKE_CODEX_DELAY_MS: "250" } });
    assert.equal(result.status, 1, result.stderr);
    const receipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/format/receipt.json"), "utf8"));
    assert.equal(receipt.status, "failed");
    assert.equal(receipt.invocations[0].timed_out, true);
    assert.equal(receipt.invocations[0].timeout_ms, 50);
    assert.match(receipt.agent.result.summary, /timed out/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner safely hands off user input with an auditable thread chain", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-resume-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    const coverDir = resolve(runDir, "cover");
    await writeSkill(skills, "baoyu-cover-image");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "article.md");
    await writeFile(input, "---\ntitle: x\nsummary: y\n---\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "cover", skill: "baoyu-cover-image", run_dir: runDir,
      input_file: input, output_dir: coverDir, mode: "confirm", constraints: { aspect_ratio: "2.35:1" }, user_preferences: {} }), "utf8");
    const waiting = { status: "needs_input", summary: "confirm", artifacts: [], decisions: [], validation: [],
      questions: [{ id: "cover_options", question: "确认推荐封面方案？" }] };
    const first = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex, "--skill-root", skills],
      { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(waiting) } });
    assert.equal(first.status, 3, first.stderr);
    const onePixelPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");
    await mkdir(resolve(coverDir, "prompts"), { recursive: true });
    const cover = resolve(coverDir, "cover.png");
    const prompt = resolve(coverDir, "prompts/01-cover.md");
    await writeFile(cover, onePixelPng);
    await writeFile(prompt, "prompt\n", "utf8");
    const completed = { status: "completed", summary: "done", artifacts: [
      { role: "cover_image", path: cover }, { role: "prompt", path: prompt },
    ], decisions: [{ name: "palette", value: "warm" }], validation: [{ name: "cover", status: "passed", details: "ok" }], questions: [] };
    const answer = resolve(root, "answer.txt");
    await writeFile(answer, "采用推荐方案", "utf8");
    const receiptPath = resolve(runDir, ".stage-runner/cover/receipt.json");
    const second = spawnSync(process.execPath, [runner, "--resume-receipt", receiptPath, "--answer-file", answer,
      "--codex-bin", fakeCodex], { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(completed) } });
    assert.equal(second.status, 0, second.stderr);
    const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
    assert.equal(receipt.status, "completed");
    assert.notEqual(receipt.agent.thread_id, "fake-thread-cover");
    assert.deepEqual(receipt.agent.thread_history, ["fake-thread-cover", receipt.agent.thread_id]);
    assert.equal(receipt.invocations.length, 2);
    assert.equal(receipt.invocations[1].resumed_from_thread_id, "fake-thread-cover");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner accepts a non-empty prompt directory", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-prompt-dir-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    const coverDir = resolve(runDir, "cover");
    await writeSkill(skills, "baoyu-cover-image");
    await mkdir(resolve(coverDir, "prompts"), { recursive: true });
    const input = resolve(runDir, "article.md");
    const cover = resolve(coverDir, "cover.png");
    await writeFile(input, "---\ntitle: x\nsummary: y\n---\n", "utf8");
    await writeFile(cover, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"));
    await writeFile(resolve(coverDir, "prompts/01-cover.md"), "prompt\n", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "cover", skill: "baoyu-cover-image", run_dir: runDir,
      input_file: input, output_dir: coverDir, mode: "skill-default", constraints: {}, user_preferences: {} }), "utf8");
    const agentResult = { status: "completed", summary: "done", artifacts: [
      { role: "cover_image", path: cover }, { role: "prompt", path: resolve(coverDir, "prompts") },
    ], decisions: [], validation: [{ name: "native", status: "passed", details: "ok" }], questions: [] };
    const result = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex, "--skill-root", skills],
      { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(agentResult) } });
    assert.equal(result.status, 0, result.stderr);
    const receipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/cover/receipt.json"), "utf8"));
    assert.equal(receipt.artifact_validation.passed, true);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Stage Runner revalidates repaired artifacts without launching another Agent", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-stage-revalidate-"));
  try {
    const skills = resolve(root, "skills");
    const runDir = resolve(root, "run");
    await writeSkill(skills, "gzh-design");
    await mkdir(runDir, { recursive: true });
    const input = resolve(runDir, "article.md");
    const body = resolve(runDir, "article.html");
    const preview = resolve(runDir, "article-preview.html");
    await writeFile(input, "---\ntitle: x\nsummary: y\n---\n", "utf8");
    await writeFile(body, '<section><span leaf="">我是 {{作者名}}</span></section>', "utf8");
    await writeFile(preview, "<html><body>preview</body></html>", "utf8");
    const request = resolve(root, "request.json");
    await writeFile(request, JSON.stringify({ stage: "layout", skill: "gzh-design", run_dir: runDir,
      input_file: input, output_dir: runDir, mode: "auto-recommended",
      constraints: { no_unresolved_placeholders: true }, user_preferences: {} }), "utf8");
    const agentResult = { status: "completed", summary: "done", artifacts: [
      { role: "body_html", path: body }, { role: "preview_html", path: preview },
    ], decisions: [], validation: [{ name: "native", status: "passed", details: "ok" }], questions: [] };
    const first = spawnSync(process.execPath, [runner, "--request", request, "--codex-bin", fakeCodex, "--skill-root", skills],
      { encoding: "utf8", env: { ...process.env, FAKE_CODEX_RESULT: JSON.stringify(agentResult) } });
    assert.equal(first.status, 1, first.stderr);
    const receiptPath = resolve(runDir, ".stage-runner/layout/receipt.json");
    const failed = JSON.parse(await readFile(receiptPath, "utf8"));
    assert.equal(failed.status, "failed");
    await writeFile(body, '<section><span leaf="">我是兮悦</span></section>', "utf8");
    const second = spawnSync(process.execPath, [runner, "--revalidate-receipt", receiptPath, "--codex-bin", "/definitely/not/codex"],
      { encoding: "utf8" });
    assert.equal(second.status, 0, second.stderr);
    const completed = JSON.parse(await readFile(receiptPath, "utf8"));
    assert.equal(completed.status, "completed");
    assert.equal(completed.invocations.length, 1);
    assert.equal(completed.revalidations.length, 1);
    assert.equal(completed.revalidations[0].previous_status, "failed");
    const history = await readdir(resolve(runDir, ".stage-runner/layout/receipt-history"));
    assert.equal(history.length, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Pipeline executes four isolated stages and writes a manifest", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-pipeline-e2e-"));
  try {
    const skills = resolve(root, "skills");
    for (const name of ["baoyu-format-markdown", "baoyu-cover-image", "baoyu-article-illustrator", "gzh-design"]) await writeSkill(skills, name);
    const source = resolve(root, "input.md");
    const runDir = resolve(root, "run");
    await writeFile(source, "# 测试文章\n\n正文\n", "utf8");
    const result = spawnSync(process.execPath, [pipeline, "--source", source, "--run-dir", runDir,
      "--codex-bin", fakeCodex, "--skill-root", skills, "--no-default-skill-roots"], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
    assert.equal(result.status, 0, result.stderr);
    const manifest = JSON.parse(await readFile(resolve(runDir, "pipeline-manifest.json"), "utf8"));
    assert.equal(manifest.status, "completed");
    assert.deepEqual(Object.keys(manifest.stages), ["format", "cover", "illustrate", "layout"]);
    for (const stage of Object.values(manifest.stages)) {
      assert.equal(stage.status, "completed");
      assert.ok(stage.thread_id);
      assert.match(stage.skill_sha256, /^[a-f0-9]{64}$/);
    }
    for (const item of Object.values(manifest.toolchain)) assert.match(item.sha256, /^[a-f0-9]{64}$/);
    const formatRequest = JSON.parse(await readFile(resolve(runDir, ".stage-runner/format/request.json"), "utf8"));
    assert.equal(formatRequest.constraints.dependency_policy, "offline-only");
    assert.equal(formatRequest.constraints.avoid_runtime_package_downloads, true);
    const coverPrompt = await readFile(resolve(runDir, ".stage-runner/cover/invocation-01-prompt.md"), "utf8");
    const illustratePrompt = await readFile(resolve(runDir, ".stage-runner/illustrate/invocation-01-prompt.md"), "utf8");
    assert.match(coverPrompt, /不要额外创建预览 HTML/);
    assert.match(illustratePrompt, /不要额外创建预览 HTML/);
    const coverReceipt = JSON.parse(await readFile(resolve(runDir, ".stage-runner/cover/receipt.json"), "utf8"));
    assert.equal(coverReceipt.invocations[0].runtime_diagnostics.minimal_runtime, false);
    assert.equal(manifest.publish.requested, false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Pipeline overlaps independent cover and illustrate stages", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-pipeline-parallel-"));
  try {
    const skills = resolve(root, "skills");
    for (const name of ["baoyu-format-markdown", "baoyu-cover-image", "baoyu-article-illustrator", "gzh-design"]) await writeSkill(skills, name);
    const source = resolve(root, "input.md");
    const runDir = resolve(root, "run");
    await writeFile(source, "# 测试文章\n\n正文\n", "utf8");
    const result = spawnSync(process.execPath, [pipeline, "--source", source, "--run-dir", runDir,
      "--codex-bin", fakeCodex, "--skill-root", skills, "--no-default-skill-roots"], {
      encoding: "utf8", maxBuffer: 16 * 1024 * 1024, env: { ...process.env, FAKE_CODEX_DELAY_MS: "200" },
    });
    assert.equal(result.status, 0, result.stderr);
    const cover = JSON.parse(await readFile(resolve(runDir, ".stage-runner/cover/receipt.json"), "utf8")).invocations[0];
    const illustrate = JSON.parse(await readFile(resolve(runDir, ".stage-runner/illustrate/receipt.json"), "utf8")).invocations[0];
    assert.ok(Date.parse(cover.started_at) < Date.parse(illustrate.completed_at));
    assert.ok(Date.parse(illustrate.started_at) < Date.parse(cover.completed_at));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Pipeline injects configured account author into layout before rendering", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-pipeline-author-"));
  try {
    const skills = resolve(root, "skills");
    for (const name of ["baoyu-format-markdown", "baoyu-cover-image", "baoyu-article-illustrator", "gzh-design"]) await writeSkill(skills, name);
    const source = resolve(root, "input.md");
    const runDir = resolve(root, "run");
    const envFile = resolve(root, ".env");
    await writeFile(source, "# 测试文章\n\n正文\n", "utf8");
    await writeFile(envFile, [
      "WECHAT_ACCOUNTS=xiyue",
      "WECHAT_XIYUE_APP_ID=test-app-id",
      "WECHAT_XIYUE_APP_SECRET=test-secret",
      "WECHAT_XIYUE_AUTHOR=兮悦",
      "",
    ].join("\n"), "utf8");
    const result = spawnSync(process.execPath, [pipeline, "--source", source, "--run-dir", runDir,
      "--codex-bin", fakeCodex, "--skill-root", skills, "--no-default-skill-roots",
      "--account", "xiyue", "--env-file", envFile], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
    assert.equal(result.status, 0, result.stderr);
    const request = JSON.parse(await readFile(resolve(runDir, ".stage-runner/layout/request.json"), "utf8"));
    assert.equal(request.constraints.author, "兮悦");
    assert.equal(request.constraints.no_unresolved_placeholders, true);
    assert.equal(request.constraints.omit_empty_author_bio, true);
    assert.equal(request.user_preferences.author, "兮悦");
    assert.equal(request.user_preferences.author_bio, "");
  } finally { await rm(root, { recursive: true, force: true }); }
});
