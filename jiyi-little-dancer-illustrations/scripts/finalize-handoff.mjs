#!/usr/bin/env node

import { mkdir, readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { cleanAnchor, parseOutline } from "./outline-utils.mjs";
import { inspectImageSet } from "./validate-images.mjs";

const SKILL_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_REFERENCE_SOURCE = join(SKILL_ROOT, "assets", "little-dancer-reference-sheet.png");
const DEFAULT_IDENTITY_ANCHORS = [
  "深色双侧小马尾",
  "浅粉蝴蝶结发带与小发髻",
  "自然圆脸和中等偏大的明亮眼睛",
  "浅粉短袖蝴蝶结上衣",
  "浅粉长裤与橙色爱心",
  "白色运动鞋",
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
    } else args[key] = true;
  }
  return args;
}

function required(args, name) {
  if (!args[name] || typeof args[name] !== "string") throw new Error(`Missing --${name}`);
  return args[name];
}

async function exists(file) {
  try {
    const info = await stat(file);
    return info.isFile();
  } catch {
    return false;
  }
}

async function atomicWrite(file, content) {
  await mkdir(dirname(file), { recursive: true });
  const temporary = `${file}.tmp-${randomUUID()}`;
  await writeFile(temporary, content, "utf8");
  await rename(temporary, file);
}

function packageRelative(packageDir, file) {
  const value = resolve(packageDir, file);
  const normalized = relative(resolve(packageDir), value);
  if (!normalized || normalized.startsWith("..")) throw new Error(`Path is outside package: ${file}`);
  return normalized;
}

function insertPlacements(article, placements) {
  const resolved = placements.map((placement) => {
    const anchor = cleanAnchor(placement.anchor);
    const index = article.indexOf(anchor);
    if (index < 0) throw new Error(`article anchor not found: ${anchor}`);
    const lineEnd = article.indexOf("\n", index);
    const paragraphEnd = article.indexOf("\n\n", index);
    const end = paragraphEnd >= 0 && (lineEnd < 0 || paragraphEnd < article.length)
      ? paragraphEnd
      : (lineEnd >= 0 ? lineEnd : article.length);
    return { ...placement, anchor, end };
  });
  const used = new Set();
  for (const placement of resolved) {
    if (used.has(placement.end)) throw new Error(`multiple images resolve to the same article position: ${placement.anchor}`);
    used.add(placement.end);
  }
  let result = article;
  for (const placement of [...resolved].sort((a, b) => b.end - a.end)) {
    result = `${result.slice(0, placement.end)}\n\n![${placement.alt}](${placement.relative_path})${result.slice(placement.end)}`;
  }
  return result;
}

const args = parseArgs(process.argv.slice(2));
try {
  const packageDir = resolve(required(args, "package-dir"));
  const formatted = packageRelative(packageDir, args["source-file"] || "formatted.md");
  const outlineFile = packageRelative(packageDir, "illustrations/outline.md");
  const outlineValidationPath = packageRelative(packageDir, "illustrations/outline-validation.json");
  const referencePackagePath = packageRelative(packageDir, "illustrations/ip-reference.png");
  const promptValidationPath = packageRelative(packageDir, "illustrations/prompt-validation.json");
  const articleFile = "article-illustrated.md";
  const outline = await readFile(resolve(packageDir, outlineFile), "utf8");
  const parsedOutline = parseOutline(outline);
  if (parsedOutline.errors.length) throw new Error(`outline is not ready: ${parsedOutline.errors.join("; ")}`);
  const outlineReport = JSON.parse(await readFile(resolve(packageDir, outlineValidationPath), "utf8"));
  if (outlineReport.passed !== true || outlineReport.shot_count !== parsedOutline.shots.length) {
    throw new Error("outline-validation.json must be passed and match the parsed outline");
  }
  const promptReport = JSON.parse(await readFile(resolve(packageDir, promptValidationPath), "utf8"));
  if (promptReport.passed !== true) throw new Error("prompt-validation.json must have passed=true");
  if (!(await exists(resolve(packageDir, referencePackagePath)))) throw new Error(`missing ${referencePackagePath}`);

  const imageValidation = await inspectImageSet({
    packageDir,
    expectedCount: parsedOutline.shots.length,
  });
  if (imageValidation.passed !== true) {
    throw new Error(`image-validation failed: ${imageValidation.errors.join("; ")}`);
  }

  const images = imageValidation.images.map((image) => image.name);
  const promptsDir = resolve(packageDir, "illustrations/prompts");
  const promptEntries = await readdir(promptsDir, { withFileTypes: true });
  const prompts = promptEntries
    .filter((entry) => entry.isFile() && /\.(?:md|txt)$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  if (images.length !== parsedOutline.shots.length) throw new Error(`outline has ${parsedOutline.shots.length} shots but package has ${images.length} images`);
  if (prompts.length !== images.length) throw new Error(`package has ${prompts.length} prompts but ${images.length} images`);

  const placements = [];
  const illustrationRecords = [];
  for (let i = 0; i < images.length; i += 1) {
    const shot = parsedOutline.shots[i];
    const relativePath = `illustrations/${images[i]}`;
    const promptFile = `illustrations/prompts/${prompts[i]}`;
    const alt = `${shot.fields["主题"]}：${shot.fields["核心意思"]}`.slice(0, 120);
    const placement = {
      index: i + 1,
      relative_path: relativePath,
      prompt_file: promptFile,
      alt,
      anchor: cleanAnchor(shot.fields["放置锚点"]),
    };
    placements.push(placement);
    illustrationRecords.push(placement);
  }

  const article = await readFile(resolve(packageDir, formatted), "utf8");
  const illustrated = insertPlacements(article, placements);
  await atomicWrite(resolve(packageDir, articleFile), illustrated);

  const backend = required(args, "backend");
  if (!/^[a-z0-9][a-z0-9._/-]*$/i.test(backend)) throw new Error("--backend must be a Skill identifier");
  const source = args["reference-source"] ? resolve(args["reference-source"]) : DEFAULT_REFERENCE_SOURCE;
  const handoff = {
    schema_version: "1.0",
    illustration_skill: "jiyi-little-dancer-illustrations",
    source_file: formatted,
    article_file: articleFile,
    count: illustrationRecords.length,
    reference_asset: {
      source,
      package_path: referencePackagePath,
      used_for_identity: true,
    },
    identity_anchors: DEFAULT_IDENTITY_ANCHORS,
    image_backend: {
      skill: backend,
      per_image: illustrationRecords.map((item) => ({
        prompt_file: item.prompt_file,
        output: item.relative_path,
        skill: backend,
        aspect: "16:9",
        reference_used_for_identity: true,
      })),
    },
    outline_validation: outlineValidationPath,
    prompt_validation: promptValidationPath,
    image_validation: "illustrations/image-validation.json",
    illustrations: illustrationRecords,
  };
  await atomicWrite(resolve(packageDir, "illustrations/illustration-handoff.json"), `${JSON.stringify(handoff, null, 2)}\n`);
  console.log(JSON.stringify({ ok: true, article_file: articleFile, handoff_file: "illustrations/illustration-handoff.json", count: illustrationRecords.length }));
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
