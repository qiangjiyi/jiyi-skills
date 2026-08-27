#!/usr/bin/env node

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { basename, dirname, extname, resolve } from "node:path";
import { homedir } from "node:os";
import { createInterface } from "node:readline/promises";
import process from "node:process";
import { fileURLToPath } from "node:url";
// Standalone publishing implementation for wechat-article-production.
import { expandHome, parseDotenv, readableFile } from "./publish-common.mjs";
import { inspectWechatHtml } from "./wechat-html.mjs";

const DEFAULT_API_BASE = "https://api.weixin.qq.com";
const RETRY_DELAYS_MS = [30_000, 60_000, 120_000];
const MAX_REMOTE_IMAGE_BYTES = 20_000_000;
const USER_AGENT = "wechat-standalone-publisher/1.0";

class PublishError extends Error {}
class RetryableError extends PublishError {}

function usage() {
  console.log(`Usage:
  node publish-wechat-article.mjs \\
    --html /absolute/article.html \\
    --cover /absolute/cover.png \\
    --title "文章标题" \\
    --account jiyi \\
    [--author "作者"] [--summary "摘要"] \\
    [--env-file /path/to/.env] [--result-file /path/to/publish-result.json] \
    [--upload-concurrency 3] \
    [--dry-run] [--yes]

The script uploads local/remote <img> sources, rewrites them to WeChat mmbiz URLs,
uploads the cover as permanent image material, then calls draft/add exactly once.
Author defaults to WECHAT_<ACCOUNT>_AUTHOR; --author overrides that value.`);
}

function parseArgs(argv) {
  const args = { yes: false, dryRun: false, uploadConcurrency: Number(process.env.WECHAT_UPLOAD_CONCURRENCY || 3) };
  const keys = new Map([
    ["--html", "html"],
    ["--cover", "cover"],
    ["--title", "title"],
    ["--author", "author"],
    ["--summary", "summary"],
    ["--account", "account"],
    ["--env-file", "envFile"],
    ["--result-file", "resultFile"],
    ["--upload-concurrency", "uploadConcurrency"],
  ]);
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") return { help: true };
    if (arg === "--yes") {
      args.yes = true;
      continue;
    }
    if (arg === "--dry-run") {
      args.dryRun = true;
      continue;
    }
    const key = keys.get(arg);
    if (!key) throw new PublishError(`unknown argument: ${arg}`);
    const value = argv[++i];
    if (!value || value.startsWith("--")) throw new PublishError(`missing value for ${arg}`);
    args[key] = value;
  }
  for (const key of ["html", "cover", "title", "account"]) {
    if (!args[key]) throw new PublishError(`missing required option: --${key}`);
  }
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
  const candidates = explicit
    ? [expandHome(explicit)]
    : [
        process.env.WECHAT_ARTICLE_PRODUCTION_ENV_FILE,
        resolve(baseDir, ".env.local"),
        resolve(baseDir, ".env"),
        resolve(homedir(), ".config/wechat-article-production/.env.local"),
        resolve(homedir(), ".config/wechat-article-production/.env"),
      ].filter(Boolean);
  let fileEnv = {};
  let used = null;
  for (const candidate of candidates) {
    const path = resolve(expandHome(candidate));
    if (await readableFile(path)) {
      fileEnv = parseDotenv(await readFile(path, "utf8"));
      used = path;
      break;
    }
  }
  return { env: { ...fileEnv, ...process.env }, used };
}

function accountKey(account, suffix) {
  const normalized = account.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
  return `WECHAT_${normalized}_${suffix}`;
}

function accountValue(env, account, suffix) {
  return account === "default" ? env[`WECHAT_${suffix}`] || "" : env[accountKey(account, suffix)] || "";
}

function assertConfiguredAccount(env, account) {
  const configured = (env.WECHAT_ACCOUNTS || "").split(",").map((x) => x.trim()).filter(Boolean);
  if (configured.length && !configured.includes(account)) {
    throw new PublishError(`account is not listed in WECHAT_ACCOUNTS: ${account}`);
  }
  const appId = accountValue(env, account, "APP_ID");
  const appSecret = accountValue(env, account, "APP_SECRET");
  if (!appId || !appSecret) throw new PublishError(`missing app id/secret for account: ${account}`);
}

function resolveAuthor(env, account, explicitAuthor) {
  const author = String(explicitAuthor || accountValue(env, account, "AUTHOR") || "").trim();
  if (!author) {
    throw new PublishError(
      `missing author for account ${account}; set ${accountKey(account, "AUTHOR")} or pass --author`,
    );
  }
  if ([...author].length > 16) throw new PublishError("author must not exceed 16 characters");
  return author;
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

async function withRetry(operation) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      if (!(error instanceof RetryableError) || attempt >= RETRY_DELAYS_MS.length) throw error;
      const delay = RETRY_DELAYS_MS[attempt];
      console.error(`network retry ${attempt + 1}/${RETRY_DELAYS_MS.length} in ${delay / 1000}s: ${error.message}`);
      await sleep(delay);
    }
  }
}

export async function mapConcurrent(items, limit, operation) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (true) {
      const index = next;
      next += 1;
      if (index >= items.length) return;
      results[index] = await operation(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

async function fetchJson(url, options, { retry = true } = {}) {
  const operation = async () => {
    let response;
    try {
      response = await fetch(url, { ...options, signal: AbortSignal.timeout(60_000) });
    } catch (error) {
      throw new RetryableError(`network error: ${error.message}`);
    }
    const raw = await response.text();
    if (!response.ok) {
      const message = `HTTP ${response.status}: ${raw.slice(0, 500)}`;
      if (response.status === 408 || response.status === 429 || response.status >= 500) {
        throw new RetryableError(message);
      }
      throw new PublishError(message);
    }
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      throw new PublishError("response was not valid JSON");
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new PublishError("response JSON must be an object");
    if (data.errcode) throw new PublishError(`WeChat error ${data.errcode}: ${data.errmsg || "unknown"}`);
    return data;
  };
  return retry ? withRetry(operation) : operation();
}

async function apiJson({ apiBase, proxyUrl, token, path, method = "POST", data, retry = true }) {
  const endpoint = new URL(path, `${apiBase.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("access_token", token);
  if (proxyUrl) {
    const envelope = { url: endpoint.toString(), method };
    if (data !== undefined) envelope.data = data;
    return fetchJson(proxyUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
      body: JSON.stringify(envelope),
    }, { retry });
  }
  return fetchJson(endpoint, {
    method,
    headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
    body: data === undefined ? undefined : JSON.stringify(data),
  }, { retry });
}

async function getAccessToken(env, account, apiBase, proxyUrl) {
  const appId = accountValue(env, account, "APP_ID");
  const appSecret = accountValue(env, account, "APP_SECRET");
  const endpoint = new URL("cgi-bin/token", `${apiBase.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("grant_type", "client_credential");
  endpoint.searchParams.set("appid", appId);
  endpoint.searchParams.set("secret", appSecret);
  const data = proxyUrl
    ? await fetchJson(proxyUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
        body: JSON.stringify({ url: endpoint.toString(), method: "GET" }),
      })
    : await fetchJson(endpoint, { method: "GET", headers: { "User-Agent": USER_AGENT } });
  if (!data.access_token) throw new PublishError("access_token missing in response");
  return String(data.access_token);
}

function mimeType(path) {
  return ({ ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp" })[extname(path).toLowerCase()] || "application/octet-stream";
}

function validateImage(data, label) {
  const png = data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpg = data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff;
  const gif = data.subarray(0, 6).toString("ascii") === "GIF87a" || data.subarray(0, 6).toString("ascii") === "GIF89a";
  const webp = data.length >= 12 && data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP";
  if (!png && !jpg && !gif && !webp) throw new PublishError(`unsupported or invalid image: ${label}`);
}

async function uploadBuffer({ apiBase, proxyUrl, token, path, fileName, type, data }) {
  const endpoint = new URL(path, `${apiBase.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("access_token", token);
  if (path.includes("add_material")) endpoint.searchParams.set("type", "image");
  if (proxyUrl) {
    return fetchJson(proxyUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": USER_AGENT },
      body: JSON.stringify({
        url: endpoint.toString(), method: "UPLOAD", fileData: data.toString("base64"),
        fileName, mimeType: type, fieldName: "media",
      }),
    });
  }
  const form = new FormData();
  form.append("media", new Blob([data], { type }), fileName);
  return fetchJson(endpoint, { method: "POST", headers: { "User-Agent": USER_AGENT }, body: form });
}

function decodeHtmlAttribute(value) {
  return value.replaceAll("&amp;", "&").replaceAll("&quot;", '"').replaceAll("&#39;", "'").replaceAll("&lt;", "<").replaceAll("&gt;", ">");
}

function inspectHtml(html) {
  const inspection = inspectWechatHtml(html);
  if (!inspection.ok) throw new PublishError(`WeChat HTML preflight failed: ${inspection.errors.join("; ")}`);
  return inspection.sources;
/*
  if (!html.trim()) throw new PublishError("article HTML is empty");
  if (Buffer.byteLength(html, "utf8") > MAX_HTML_BYTES) throw new PublishError(`article HTML exceeds ${MAX_HTML_BYTES} bytes`);
  if (/<!doctype\b|<\/?(?:html|head|body)(?:\s|>)/i.test(html)) throw new PublishError("article HTML must be a body fragment");
  const placeholder = html.match(/\{\{[^}]+\}\}|【[^】]*(?:待补|插入)[^】]*】/);
  if (placeholder) throw new PublishError(`article HTML contains unresolved placeholder: ${placeholder[0]}`);
  const sources = [];
  for (const tag of html.matchAll(/<img\b[^>]*>/gis)) {
    const src = tag[0].match(/(?:^|\s)src\s*=\s*(?:(["'])(.*?)\1|([^\s>]+))/is);
    if (!src) throw new PublishError("every <img> must have a non-empty src");
    sources.push(decodeHtmlAttribute(src[2] || src[3] || ""));
  }
  return sources;
*/
}

export function normalizeHtmlForWechatApi(html) {
  return String(html)
    .replace(/\r\n?/g, "\n")
    .replace(/>\s*\n[\t ]*</g, "><")
    .trim();
}

function isWechatImage(source) {
  try {
    const url = new URL(source);
    return ["http:", "https:"].includes(url.protocol) && url.hostname.startsWith("mmbiz") && (url.hostname.endsWith(".qpic.cn") || url.hostname.endsWith(".qlogo.cn"));
  } catch {
    return false;
  }
}

async function loadImage(source, baseDir) {
  if (/^https?:\/\//i.test(source)) {
    const response = await withRetry(async () => {
      try {
        const result = await fetch(source, { headers: { "User-Agent": USER_AGENT }, signal: AbortSignal.timeout(60_000) });
        if (!result.ok) {
          if (result.status === 408 || result.status === 429 || result.status >= 500) throw new RetryableError(`image download HTTP ${result.status}`);
          throw new PublishError(`image download HTTP ${result.status}`);
        }
        return result;
      } catch (error) {
        if (error instanceof PublishError) throw error;
        throw new RetryableError(`image download failed: ${error.message}`);
      }
    });
    const data = Buffer.from(await response.arrayBuffer());
    if (!data.length || data.length > MAX_REMOTE_IMAGE_BYTES) throw new PublishError(`remote image has invalid size: ${source}`);
    const name = basename(new URL(source).pathname) || "remote-image.png";
    validateImage(data, source);
    return { data, name, type: response.headers.get("content-type") || mimeType(name) };
  }
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(source) && !source.startsWith("file:")) throw new PublishError(`unsupported image source: ${source}`);
  const raw = source.startsWith("file:") ? decodeURIComponent(new URL(source).pathname) : source.split(/[?#]/, 1)[0];
  const path = resolve(baseDir, raw);
  const data = await readFile(path);
  validateImage(data, path);
  return { data, name: basename(path), type: mimeType(path) };
}

function rewriteImageSources(html, replacements) {
  return html.replace(/<img\b[^>]*>/gis, (tag) => tag.replace(/((?:^|\s)src\s*=\s*)(?:(["'])(.*?)\2|([^\s>]+))/is, (whole, prefix, quote, quoted, bare) => {
    const source = decodeHtmlAttribute(quoted || bare || "");
    const replacement = replacements.get(source);
    if (!replacement) return whole;
    const q = quote || '"';
    return `${prefix}${q}${replacement.replaceAll("&", "&amp;").replaceAll(q, q === '"' ? "&quot;" : "&#39;")}${q}`;
  }));
}

async function confirm(args, author, imageCount, proxyEnabled) {
  console.log("Ready to create WeChat draft:");
  console.log(`  account: ${args.account}`);
  console.log(`  author: ${author}`);
  console.log(`  title: ${args.title}`);
  console.log(`  body images: ${imageCount}`);
  console.log(`  cover: ${resolve(expandHome(args.cover))}`);
  console.log(`  proxy: ${proxyEnabled ? "yes" : "no"}`);
  if (args.yes) return;
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const answer = (await rl.question("Create draft now? Type 'yes' to continue: ")).trim().toLowerCase();
  rl.close();
  if (answer !== "yes") throw new PublishError("cancelled");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return usage();
  const htmlPath = resolve(expandHome(args.html));
  const coverPath = resolve(expandHome(args.cover));
  if (!(await readableFile(htmlPath))) throw new PublishError(`HTML not found: ${htmlPath}`);
  if (!(await readableFile(coverPath))) throw new PublishError(`cover not found: ${coverPath}`);
  const sourceHtml = await readFile(htmlPath, "utf8");
  const html = normalizeHtmlForWechatApi(sourceHtml);
  const sources = inspectHtml(html);
  const preparedImages = new Map();
  for (const source of sources) {
    if (!isWechatImage(source) && !preparedImages.has(source)) {
      // Dry-run validates local files without downloading remote images. Remote
      // image reachability is checked only in the real publish phase.
      if (!args.dryRun || !/^https?:\/\//i.test(source)) {
        preparedImages.set(source, await loadImage(source, dirname(htmlPath)));
      }
    }
  }
  const coverData = await readFile(coverPath);
  validateImage(coverData, coverPath);
  const { env, used } = await loadEnv(dirname(htmlPath), args.envFile);
  assertConfiguredAccount(env, args.account);
  const author = resolveAuthor(env, args.account, args.author);
  const apiBase = (env.WECHAT_API_BASE || DEFAULT_API_BASE).trim();
  const proxyUrl = (env.WECHAT_PROXY_URL || "").trim();
  const plan = {
    account: args.account, title: args.title, author, summary: args.summary || "",
    html: htmlPath, cover: coverPath, bodyImageCount: sources.length, envFile: used, proxy: Boolean(proxyUrl),
    uploadConcurrency: args.uploadConcurrency,
    remoteImagesDeferred: args.dryRun && sources.some((source) => /^https?:\/\//i.test(source) && !isWechatImage(source)),
  };
  if (args.dryRun) {
    plan.inputsValidated = true;
    if (args.resultFile) {
      await writeJsonAtomic(args.resultFile, { ...plan, ok: false, dry_run: true });
    }
    console.log(JSON.stringify(plan, null, 2));
    return;
  }
  await confirm(args, author, sources.length, Boolean(proxyUrl));
  console.log("[1/4] resolve access token");
  const token = await getAccessToken(env, args.account, apiBase, proxyUrl);
  const replacements = new Map();
  const uploadSources = [...new Set(sources.filter((source) => !isWechatImage(source)))];
  const bodyUploads = mapConcurrent(uploadSources, args.uploadConcurrency, async (source, index) => {
    const image = preparedImages.get(source);
    if (!image) throw new PublishError(`body image was not prepared: ${source}`);
    console.log(`[2/4] upload body image ${index + 1}/${uploadSources.length}: ${image.name}`);
    const result = await uploadBuffer({ apiBase, proxyUrl, token, path: "cgi-bin/media/uploadimg", fileName: image.name, type: image.type, data: image.data });
    if (!result.url) throw new PublishError(`uploadimg returned no URL for ${image.name}`);
    replacements.set(source, String(result.url));
  });
  console.log(`[3/4] upload cover: ${basename(coverPath)}`);
  const coverUpload = uploadBuffer({ apiBase, proxyUrl, token, path: "cgi-bin/material/add_material", fileName: basename(coverPath), type: mimeType(coverPath), data: coverData });
  const [, coverResult] = await Promise.all([bodyUploads, coverUpload]);
  const rewrittenHtml = rewriteImageSources(html, replacements);
  inspectHtml(rewrittenHtml);
  if (!coverResult.media_id) throw new PublishError("cover upload returned no media_id");
  const article = {
    article_type: "news",
    title: args.title,
    author,
    content: rewrittenHtml,
    thumb_media_id: String(coverResult.media_id),
    need_open_comment: 1,
    only_fans_can_comment: 0,
  };
  if (args.summary) article.digest = args.summary.slice(0, 120);
  console.log("[4/4] create draft (single attempt, no automatic retry)");
  const draft = await apiJson({ apiBase, proxyUrl, token, path: "cgi-bin/draft/add", data: { articles: [article] }, retry: false });
  if (!draft.media_id) throw new PublishError("draft/add returned no media_id");
  const draftMediaId = String(draft.media_id);
  const result = {
    ok: true,
    account: args.account,
    author,
    title: args.title,
    body_image_count: sources.length,
    draft_media_id: draftMediaId,
    published_at: new Date().toISOString(),
  };
  if (args.resultFile) {
    result.result_file = resolve(expandHome(args.resultFile));
    await writeJsonAtomic(result.result_file, result);
  }
  console.log(`Draft Media ID: ${draftMediaId}`);
  console.log(JSON.stringify(result, null, 2));
}

function isMainModule() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
  }
}

if (isMainModule()) {
  main().catch((error) => {
    console.error(`error: ${error.message}`);
    process.exitCode = 1;
  });
}
