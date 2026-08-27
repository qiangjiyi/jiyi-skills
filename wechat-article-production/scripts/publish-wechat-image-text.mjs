#!/usr/bin/env node

import { mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { basename, dirname, extname, resolve } from "node:path";
import { homedir } from "node:os";
import { createInterface } from "node:readline/promises";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { expandHome, parseDotenv, readableFile } from "./publish-common.mjs";

const DEFAULT_API_BASE = "https://api.weixin.qq.com";
const RETRY_DELAYS_MS = [30_000, 60_000, 120_000];
const MAX_IMAGE_COUNT = 20;
const MAX_IMAGE_BYTES = 10_000_000;
const USER_AGENT = "wechat-image-text-draft/1.0";
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".bmp"]);

class PublishError extends Error {}
class RetryableError extends PublishError {}

function usage() {
  console.log(`Usage:
  node publish-wechat-image-text.mjs \\
    --title "贴图标题" \\
    --images-dir /absolute/cards \\
    --account jiyi \\
    [--caption-file /absolute/caption.txt | --caption "纯文本说明"] \\
    [--env-file /path/to/.env] [--result-file /path/to/publish-result.json] \\
    [--upload-concurrency 3] [--dry-run] [--yes]

Creates one WeChat draft with article_type=newspic. It never calls a formal
publish endpoint. Images are uploaded as permanent material; 1-20 PNG/JPG/GIF/BMP
files are accepted in lexicographic filename order. The first image becomes the cover.`);
}

function parseArgs(argv) {
  const args = { yes: false, dryRun: false, uploadConcurrency: Number(process.env.WECHAT_UPLOAD_CONCURRENCY || 3), images: [] };
  const keys = new Map([
    ["--title", "title"], ["--caption", "caption"], ["--caption-file", "captionFile"],
    ["--images-dir", "imagesDir"], ["--image", "image"], ["--account", "account"],
    ["--env-file", "envFile"], ["--result-file", "resultFile"], ["--upload-concurrency", "uploadConcurrency"],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--help" || arg === "-h") return { help: true };
    if (arg === "--yes") { args.yes = true; continue; }
    if (arg === "--dry-run") { args.dryRun = true; continue; }
    const key = keys.get(arg);
    if (!key) throw new PublishError(`unknown argument: ${arg}`);
    const value = argv[++index];
    if (!value || value.startsWith("--")) throw new PublishError(`missing value for ${arg}`);
    if (key === "image") args.images.push(value);
    else args[key] = value;
  }
  for (const key of ["title", "account"]) if (!args[key]) throw new PublishError(`missing required option: --${key}`);
  if (args.imagesDir && args.images.length) throw new PublishError("use either --images-dir or repeated --image, not both");
  if (!args.imagesDir && !args.images.length) throw new PublishError("provide --images-dir or at least one --image");
  if (args.caption && args.captionFile) throw new PublishError("use either --caption or --caption-file, not both");
  if ([...args.title].length > 32) throw new PublishError("title must not exceed 32 characters");
  args.uploadConcurrency = Number(args.uploadConcurrency);
  if (!Number.isSafeInteger(args.uploadConcurrency) || args.uploadConcurrency < 1 || args.uploadConcurrency > 8) {
    throw new PublishError("upload concurrency must be an integer from 1 to 8");
  }
  return args;
}

async function writeJsonAtomic(path, value) {
  const target = resolve(expandHome(path));
  await mkdir(dirname(target), { recursive: true });
  const temporary = `${target}.tmp-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, target);
  return target;
}

async function loadEnv(baseDir, explicit) {
  const candidates = explicit ? [expandHome(explicit)] : [
    process.env.WECHAT_ARTICLE_PRODUCTION_ENV_FILE, resolve(baseDir, ".env.local"), resolve(baseDir, ".env"),
    resolve(homedir(), ".config/wechat-article-production/.env.local"), resolve(homedir(), ".config/wechat-article-production/.env"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    const path = resolve(expandHome(candidate));
    if (await readableFile(path)) return { env: { ...parseDotenv(await readFile(path, "utf8")), ...process.env }, used: path };
  }
  return { env: { ...process.env }, used: null };
}

function accountKey(account, suffix) {
  const normalized = account.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
  return `WECHAT_${normalized}_${suffix}`;
}

function accountValue(env, account, suffix) {
  return account === "default" ? env[`WECHAT_${suffix}`] || "" : env[accountKey(account, suffix)] || "";
}

function assertConfiguredAccount(env, account) {
  const configured = (env.WECHAT_ACCOUNTS || "").split(",").map((item) => item.trim()).filter(Boolean);
  if (configured.length && !configured.includes(account)) throw new PublishError(`account is not listed in WECHAT_ACCOUNTS: ${account}`);
  if (!accountValue(env, account, "APP_ID") || !accountValue(env, account, "APP_SECRET")) {
    throw new PublishError(`missing app id/secret for account: ${account}`);
  }
}

function sleep(ms) { return new Promise((done) => setTimeout(done, ms)); }

async function withRetry(operation) {
  for (let attempt = 0; ; attempt += 1) {
    try { return await operation(); }
    catch (error) {
      if (!(error instanceof RetryableError) || attempt >= RETRY_DELAYS_MS.length) throw error;
      const delay = RETRY_DELAYS_MS[attempt];
      console.error(`network retry ${attempt + 1}/${RETRY_DELAYS_MS.length} in ${delay / 1000}s: ${error.message}`);
      await sleep(delay);
    }
  }
}

async function fetchJson(url, options, { retry = true } = {}) {
  const operation = async () => {
    let response;
    try { response = await fetch(url, { ...options, signal: AbortSignal.timeout(60_000) }); }
    catch (error) { throw new RetryableError(`network error: ${error.message}`); }
    const raw = await response.text();
    if (!response.ok) {
      const message = `HTTP ${response.status}: ${raw.slice(0, 500)}`;
      if (response.status === 408 || response.status === 429 || response.status >= 500) throw new RetryableError(message);
      throw new PublishError(message);
    }
    let data;
    try { data = JSON.parse(raw); } catch { throw new PublishError("response was not valid JSON"); }
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new PublishError("response JSON must be an object");
    if (data.errcode) throw new PublishError(`WeChat error ${data.errcode}: ${data.errmsg || "unknown"}`);
    return data;
  };
  return retry ? withRetry(operation) : operation();
}

async function apiJson({ apiBase, proxyUrl, token, path, data, retry = true }) {
  const endpoint = new URL(path, `${apiBase.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("access_token", token);
  if (proxyUrl) {
    return fetchJson(proxyUrl, { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT }, body: JSON.stringify({ url: endpoint.toString(), method: "POST", data }) }, { retry });
  }
  return fetchJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT }, body: JSON.stringify(data) }, { retry });
}

async function getAccessToken(env, account, apiBase, proxyUrl) {
  const endpoint = new URL("cgi-bin/token", `${apiBase.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("grant_type", "client_credential");
  endpoint.searchParams.set("appid", accountValue(env, account, "APP_ID"));
  endpoint.searchParams.set("secret", accountValue(env, account, "APP_SECRET"));
  const data = proxyUrl
    ? await fetchJson(proxyUrl, { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT }, body: JSON.stringify({ url: endpoint.toString(), method: "GET" }) })
    : await fetchJson(endpoint, { method: "GET", headers: { "User-Agent": USER_AGENT } });
  if (!data.access_token) throw new PublishError("access_token missing in response");
  return String(data.access_token);
}

function mimeType(path) {
  return ({ ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".bmp": "image/bmp" })[extname(path).toLowerCase()] || "application/octet-stream";
}

function validateImage(data, label) {
  const png = data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpg = data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff;
  const gif = data.subarray(0, 6).toString("ascii") === "GIF87a" || data.subarray(0, 6).toString("ascii") === "GIF89a";
  const bmp = data.length >= 2 && data.subarray(0, 2).toString("ascii") === "BM";
  if (!png && !jpg && !gif && !bmp) throw new PublishError(`unsupported or invalid image: ${label}`);
  if (data.length > MAX_IMAGE_BYTES) throw new PublishError(`image exceeds ${MAX_IMAGE_BYTES} bytes: ${label}`);
}

async function imagePaths(args) {
  let paths;
  if (args.imagesDir) {
    const directory = resolve(expandHome(args.imagesDir));
    let entries;
    try { entries = await readdir(directory, { withFileTypes: true }); }
    catch { throw new PublishError(`images directory not found: ${directory}`); }
    paths = entries.filter((entry) => entry.isFile() && IMAGE_EXTENSIONS.has(extname(entry.name).toLowerCase())).map((entry) => resolve(directory, entry.name)).sort((a, b) => basename(a).localeCompare(b, "en"));
  } else {
    paths = args.images.map((path) => resolve(expandHome(path)));
  }
  if (paths.length < 1 || paths.length > MAX_IMAGE_COUNT) throw new PublishError(`newspic requires 1-${MAX_IMAGE_COUNT} images; received ${paths.length}`);
  for (const path of paths) {
    if (!(await readableFile(path))) throw new PublishError(`image not found: ${path}`);
    if (!IMAGE_EXTENSIONS.has(extname(path).toLowerCase())) throw new PublishError(`unsupported image extension: ${path}`);
  }
  return paths;
}

async function mapConcurrent(items, limit, operation) {
  const results = new Array(items.length); let next = 0;
  async function worker() { while (true) { const index = next++; if (index >= items.length) return; results[index] = await operation(items[index], index); } }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

async function uploadPermanentImage({ apiBase, proxyUrl, token, path, data }) {
  const endpoint = new URL("cgi-bin/material/add_material", `${apiBase.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("access_token", token);
  endpoint.searchParams.set("type", "image");
  if (proxyUrl) {
    return fetchJson(proxyUrl, { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT }, body: JSON.stringify({ url: endpoint.toString(), method: "UPLOAD", fileData: data.toString("base64"), fileName: basename(path), mimeType: mimeType(path), fieldName: "media" }) });
  }
  const form = new FormData();
  form.append("media", new Blob([data], { type: mimeType(path) }), basename(path));
  return fetchJson(endpoint, { method: "POST", headers: { "User-Agent": USER_AGENT }, body: form });
}

async function readCaption(args) {
  const caption = args.captionFile ? await readFile(resolve(expandHome(args.captionFile)), "utf8") : (args.caption || "");
  const normalized = caption.replace(/\r\n?/g, "\n").trim();
  if (/<\/?[A-Za-z][^>]*>|!\[[^\]]*\]\([^)]*\)|\[[^\]]+\]\([^)]*\)|(^|\s)(?:#{1,6}\s|[-*+]\s|\d+\.\s)|\*{1,3}[^*]+\*{1,3}|_{1,3}[^_]+_{1,3}|`[^`]+`/.test(normalized)) throw new PublishError("caption must be plain text, not HTML or Markdown");
  return normalized;
}

async function confirm(args, images, proxyEnabled) {
  console.log("Ready to create WeChat image-text draft:");
  console.log(`  account: ${args.account}`); console.log(`  title: ${args.title}`); console.log(`  images: ${images.length} (first image is cover)`); console.log(`  proxy: ${proxyEnabled ? "yes" : "no"}`);
  if (args.yes) return;
  const readline = createInterface({ input: process.stdin, output: process.stdout });
  const answer = (await readline.question("Create draft now? Type 'yes' to continue: ")).trim().toLowerCase();
  readline.close();
  if (answer !== "yes") throw new PublishError("cancelled");
}

async function main() {
  const args = parseArgs(process.argv.slice(2)); if (args.help) return usage();
  const paths = await imagePaths(args);
  const files = await Promise.all(paths.map(async (path) => { const data = await readFile(path); validateImage(data, path); return { path, data }; }));
  const caption = await readCaption(args);
  const { env, used } = await loadEnv(dirname(paths[0]), args.envFile);
  assertConfiguredAccount(env, args.account);
  const apiBase = (env.WECHAT_API_BASE || DEFAULT_API_BASE).trim();
  const proxyUrl = (env.WECHAT_PROXY_URL || "").trim();
  const plan = { ok: false, dry_run: Boolean(args.dryRun), target: "newspic", account: args.account, title: args.title, caption_length: [...caption].length, image_count: files.length, images: paths, first_image_is_cover: true, env_file: used, proxy: Boolean(proxyUrl), upload_concurrency: args.uploadConcurrency };
  if (args.dryRun) { plan.inputs_validated = true; if (args.resultFile) await writeJsonAtomic(args.resultFile, plan); console.log(JSON.stringify(plan, null, 2)); return; }
  await confirm(args, files, Boolean(proxyUrl));
  console.log("[1/3] resolve access token");
  const token = await getAccessToken(env, args.account, apiBase, proxyUrl);
  const imageMediaIds = await mapConcurrent(files, args.uploadConcurrency, async (file, index) => {
    console.log(`[2/3] upload image ${index + 1}/${files.length}: ${basename(file.path)}`);
    const uploaded = await uploadPermanentImage({ apiBase, proxyUrl, token, ...file });
    if (!uploaded.media_id) throw new PublishError(`permanent material upload returned no media_id: ${basename(file.path)}`);
    return String(uploaded.media_id);
  });
  const article = { article_type: "newspic", title: args.title, content: caption, need_open_comment: 1, only_fans_can_comment: 0, image_info: { image_list: imageMediaIds.map((image_media_id) => ({ image_media_id })) } };
  console.log("[3/3] create image-text draft (single attempt, no automatic retry)");
  const draft = await apiJson({ apiBase, proxyUrl, token, path: "cgi-bin/draft/add", data: { articles: [article] }, retry: false });
  if (!draft.media_id) throw new PublishError("draft/add returned no media_id");
  const result = { ok: true, target: "newspic", account: args.account, title: args.title, caption_length: [...caption].length, image_count: files.length, first_image_is_cover: true, uploaded_image_media_ids: imageMediaIds, draft_media_id: String(draft.media_id), created_at: new Date().toISOString() };
  if (args.resultFile) { result.result_file = resolve(expandHome(args.resultFile)); await writeJsonAtomic(result.result_file, result); }
  console.log(`Draft Media ID: ${result.draft_media_id}`); console.log(JSON.stringify(result, null, 2));
}

function isMainModule() {
  if (!process.argv[1]) return false;
  try { return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url)); }
}

if (isMainModule()) main().catch((error) => { console.error(`error: ${error.message}`); process.exitCode = 1; });
