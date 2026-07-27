import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { mapConcurrent, normalizeHtmlForWechatApi } from "../scripts/publish-wechat-article.mjs";

const projectRoot = resolve(import.meta.dirname, "..");
const preflightScript = resolve(projectRoot, "scripts/preflight.mjs");
const publisherScript = resolve(projectRoot, "scripts/publish-wechat-article.mjs");
const configScript = resolve(projectRoot, "scripts/resolve-config.mjs");
const coverToolsScript = resolve(projectRoot, "scripts/cover-tools.mjs");
const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
const requiredSkills = [
  "baoyu-format-markdown",
  "baoyu-cover-image",
  "baoyu-article-illustrator",
  "gzh-design",
];

function run(script, args) {
  return spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
}

async function writeSkill(root, name) {
  const directory = resolve(root, `${name}-folder`);
  await mkdir(directory, { recursive: true });
  await writeFile(resolve(directory, "SKILL.md"), `---\nname: ${name}\ndescription: test\n---\n`, "utf8");
}

test("skill preflight passes only when every required skill exists", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-skills-pass-"));
  try {
    for (const name of requiredSkills) await writeSkill(root, name);
    const result = run(preflightScript, ["skills", "--no-default-roots", "--skill-root", root, "--json"]);
    assert.equal(result.status, 0, result.stderr);
    const output = JSON.parse(result.stdout);
    assert.equal(output.ok, true);
    assert.equal(output.found.length, requiredSkills.length);
    assert.deepEqual(output.missing, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("skill preflight reports all missing skills and exits before work", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-skills-fail-"));
  try {
    await writeSkill(root, requiredSkills[0]);
    const result = run(preflightScript, ["skills", "--no-default-roots", "--skill-root", root, "--json"]);
    assert.equal(result.status, 1);
    const output = JSON.parse(result.stdout);
    assert.equal(output.ok, false);
    assert.deepEqual(output.missing.map((item) => item.name), requiredSkills.slice(1));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("account preflight validates presence without exposing credentials", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-account-pass-"));
  const envFile = resolve(root, ".env");
  try {
    await writeFile(envFile, [
      "WECHAT_ACCOUNTS=xiyue",
      "WECHAT_XIYUE_APP_ID=test-app-id",
      "WECHAT_XIYUE_APP_SECRET=super-secret-value",
      "WECHAT_XIYUE_AUTHOR=兮悦",
      "",
    ].join("\n"), "utf8");
    const result = run(preflightScript, ["account", "--account", "xiyue", "--env-file", envFile, "--json"]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.includes("super-secret-value"), false);
    const output = JSON.parse(result.stdout);
    assert.deepEqual({ ok: output.ok, account: output.account, author: output.author }, {
      ok: true,
      account: "xiyue",
      author: "兮悦",
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("account preflight fails when author mapping is absent", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-account-fail-"));
  const envFile = resolve(root, ".env");
  try {
    await writeFile(envFile, [
      "WECHAT_ACCOUNTS=xiyue",
      "WECHAT_XIYUE_APP_ID=test-app-id",
      "WECHAT_XIYUE_APP_SECRET=super-secret-value",
      "",
    ].join("\n"), "utf8");
    const result = run(preflightScript, ["account", "--account", "xiyue", "--env-file", envFile]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /AUTHOR/);
    assert.equal(result.stderr.includes("super-secret-value"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("execution modes default to downstream auto-recommended flow", () => {
  const result = run(configScript, ["mode", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.illustration_mode, "auto-recommended");
  assert.equal(output.layout_mode, "auto-recommended");
});

test("execution mode env file can restore native confirmations per stage", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-execution-mode-"));
  const envFile = resolve(root, ".env");
  try {
    await writeFile(envFile, [
      "WECHAT_PIPELINE_RECOMMENDATION_MODE=auto-recommended",
      "WECHAT_PIPELINE_LAYOUT_MODE=confirm",
      "",
    ].join("\n"), "utf8");
    const result = run(configScript, ["mode", "--env-file", envFile, "--json"]);
    assert.equal(result.status, 0, result.stderr);
    const output = JSON.parse(result.stdout);
    assert.equal(output.illustration_mode, "auto-recommended");
    assert.equal(output.layout_mode, "confirm");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("cover adapter removes identical prompt aliases without assuming dependency filenames", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-cover-artifacts-"));
  const prompts = resolve(root, "prompts");
  try {
    await mkdir(prompts, { recursive: true });
    await writeFile(resolve(root, "cover.png"), onePixelPng);
    await writeFile(resolve(prompts, "01-cover-test-topic.md"), "final prompt\n", "utf8");
    await writeFile(resolve(prompts, "cover.md"), "final prompt\n", "utf8");
    const duplicate = run(coverToolsScript, ["validate", "--cover-dir", root]);
    assert.equal(duplicate.status, 1);
    assert.match(duplicate.stderr, /Duplicate prompt content/);

    const normalized = run(coverToolsScript, ["normalize", "--cover-dir", root]);
    assert.equal(normalized.status, 0, normalized.stderr);
    assert.deepEqual(JSON.parse(normalized.stdout).removed, ["cover.md"]);
    const clean = run(coverToolsScript, ["validate", "--cover-dir", root]);
    assert.equal(clean.status, 0, clean.stderr);

    await writeFile(resolve(prompts, "retry.md"), "different revised prompt\n", "utf8");
    const distinct = run(coverToolsScript, ["normalize", "--cover-dir", root]);
    assert.equal(distinct.status, 0, distinct.stderr);
    assert.deepEqual(JSON.parse(distinct.stdout).removed, []);
    const distinctValid = run(coverToolsScript, ["validate", "--cover-dir", root]);
    assert.equal(distinctValid.status, 0, distinctValid.stderr);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("optional publisher dry-run validates final local inputs without creating a draft", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-publisher-dry-run-"));
  const envFile = resolve(root, ".env");
  const htmlFile = resolve(root, "article.html");
  const coverFile = resolve(root, "cover.png");
  const bodyFile = resolve(root, "body.png");
  try {
    await writeFile(envFile, [
      "WECHAT_ACCOUNTS=xiyue",
      "WECHAT_XIYUE_APP_ID=test-app-id",
      "WECHAT_XIYUE_APP_SECRET=super-secret-value",
      "WECHAT_XIYUE_AUTHOR=兮悦",
      "",
    ].join("\n"), "utf8");
    await writeFile(coverFile, onePixelPng);
    await writeFile(bodyFile, onePixelPng);
    await writeFile(htmlFile, '<section><span leaf=""><img src="body.png"></span></section>', "utf8");
    const result = run(publisherScript, [
      "--html", htmlFile,
      "--cover", coverFile,
      "--title", "测试文章",
      "--account", "xiyue",
      "--env-file", envFile,
      "--dry-run",
    ]);
    assert.equal(result.status, 0, result.stderr);
    const output = JSON.parse(result.stdout);
    assert.equal(output.inputsValidated, true);
    assert.equal(output.bodyImageCount, 1);
    assert.equal(output.uploadConcurrency, 3);
    assert.equal(result.stdout.includes("super-secret-value"), false);
    assert.equal(result.stdout.includes("Draft Media ID"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("publisher rejects unreadable body images before any WeChat API call", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "wechat-publisher-invalid-"));
  const envFile = resolve(root, ".env");
  const htmlFile = resolve(root, "article.html");
  const coverFile = resolve(root, "cover.png");
  try {
    await writeFile(envFile, [
      "WECHAT_ACCOUNTS=xiyue",
      "WECHAT_XIYUE_APP_ID=test-app-id",
      "WECHAT_XIYUE_APP_SECRET=super-secret-value",
      "WECHAT_XIYUE_AUTHOR=兮悦",
      "",
    ].join("\n"), "utf8");
    await writeFile(coverFile, onePixelPng);
    await writeFile(htmlFile, '<section><span leaf=""><img src="missing.png"></span></section>', "utf8");
    const result = run(publisherScript, [
      "--html", htmlFile,
      "--cover", coverFile,
      "--title", "测试文章",
      "--account", "xiyue",
      "--env-file", envFile,
      "--yes",
    ]);
    assert.equal(result.status, 1);
    assert.equal(result.stdout.includes("[1/4] resolve access token"), false);
    assert.equal(result.stderr.includes("super-secret-value"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("publisher bounds concurrent uploads without changing result order", async () => {
  let active = 0;
  let peak = 0;
  const results = await mapConcurrent([1, 2, 3, 4, 5, 6], 3, async (value) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((done) => setTimeout(done, 20));
    active -= 1;
    return value * 2;
  });
  assert.equal(peak, 3);
  assert.deepEqual(results, [2, 4, 6, 8, 10, 12]);
});

test("publisher removes formatting-only newlines before WeChat API submission", () => {
  const source = `<section>\n  <p>\n    <span leaf="">甲</span> <span leaf="">乙</span>\n  </p>\n</section>\n`;
  const normalized = normalizeHtmlForWechatApi(source);
  assert.equal(normalized, `<section><p><span leaf="">甲</span> <span leaf="">乙</span></p></section>`);
  assert.equal(normalizeHtmlForWechatApi(normalized), normalized);
});
