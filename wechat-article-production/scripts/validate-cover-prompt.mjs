#!/usr/bin/env node

import { homedir } from "node:os";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { parseArgs, required, atomicWrite } from "./shared.mjs";

const TEXT_LEVELS = new Set(["none", "title-only", "title-subtitle", "text-rich"]);
const TYPES = new Set(["hero", "conceptual", "typography", "metaphor", "scene", "minimal"]);
const PALETTES = new Set(["warm", "elegant", "cool", "dark", "earth", "vivid", "pastel", "mono", "retro", "duotone", "macaron"]);
const RENDERINGS = new Set(["flat-vector", "hand-drawn", "painterly", "digital", "pixel", "chalk", "screen-print"]);
const MOODS = new Set(["subtle", "balanced", "bold"]);
const FONTS = new Set(["clean", "handwritten", "serif", "display"]);
const REQUIRED_FRONTMATTER = ["type", "palette", "rendering", "text", "mood", "font", "aspect", "lang"];
const REQUIRED_SECTIONS = [
  "# Content Context",
  "# Visual Design",
  "# Text Elements",
  "# Mood Application",
  "# Font Application",
  "# Composition",
];
const REQUIRED_FIELDS = [
  "Article title",
  "Content summary",
  "Keywords",
  "Cover theme",
  "Type",
  "Palette",
  "Rendering",
  "Font",
  "Text level",
  "Mood",
  "Aspect ratio",
  "Language",
  "Type composition",
  "Visual composition",
  "Main visual",
  "Layout",
  "Decorative",
  "Color scheme",
  "Color constraint",
  "Rendering notes",
  "Type notes",
  "Palette notes",
];

function scalar(value) {
  return String(value || "").trim().replace(/^(["'])(.*)\1$/, "$2");
}

function parseDocument(content) {
  const match = content.match(/^---\s*\n([\s\S]*?)\n---\s*(?:\n|$)/);
  if (!match) return { fields: {}, body: content, error: "missing YAML frontmatter" };
  const fields = {};
  for (const line of match[1].split(/\r?\n/)) {
    const field = line.match(/^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$/);
    if (field) fields[field[1]] = scalar(field[2]);
  }
  return { fields, body: content.slice(match[0].length) };
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function lineValue(body, label) {
  const match = body.match(new RegExp(`^\\s*(?:[-*]\\s*)?${escapeRegExp(label)}\\s*:\\s*(.+?)\\s*$`, "mi"));
  return match ? match[1].trim() : null;
}

function sectionBody(body, heading) {
  const headingPattern = new RegExp(`^${escapeRegExp(heading)}\\s*$`, "mi");
  const match = headingPattern.exec(body);
  if (!match) return null;
  const rest = body.slice(match.index + match[0].length);
  return rest.split(/^#\s+/m, 1)[0].trim();
}

function normalizeAspect(value) {
  return String(value || "").replace(/[．。]/g, ".").replace(/[：]/g, ":").replace(/\s+/g, "");
}

function relativePackagePath(packageDir, file) {
  const normalized = relative(resolve(packageDir), resolve(packageDir, file));
  if (!normalized || normalized.startsWith("..")) throw new Error(`Path is outside package: ${file}`);
  return normalized;
}

async function configuredText(packageDir) {
  try {
    const manifest = JSON.parse(await readFile(join(packageDir, "execution-manifest.json"), "utf8"));
    const override = manifest.scope?.cover_text_override;
    if (override && TEXT_LEVELS.has(override)) return { value: override, source: "execution-manifest.current-request-override" };
  } catch {
    // Standalone CoverImage use may not have a pipeline manifest.
  }
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
      return { value: match && TEXT_LEVELS.has(match[1]) ? match[1] : null, source: file };
    } catch {
      // Continue through the documented preference precedence.
    }
  }
  return { value: null, source: null };
}

function sourceTitle(sourceContent) {
  const parsed = parseDocument(sourceContent);
  return parsed.fields.title || null;
}

function validate({ prompt, source, expectedAspect, expectedText }) {
  const errors = [];
  const checks = [];
  const add = (name, passed, detail) => {
    checks.push({ name, passed, detail });
    if (!passed) errors.push(detail);
  };
  const parsed = parseDocument(prompt);
  add("frontmatter", !parsed.error, parsed.error || "YAML frontmatter found");

  for (const field of REQUIRED_FRONTMATTER) {
    add(`frontmatter:${field}`, Boolean(parsed.fields[field]), `frontmatter field ${field} is ${parsed.fields[field] ? "present" : "missing"}`);
  }
  if (parsed.fields.type) add("frontmatter:type-value", TYPES.has(parsed.fields.type), `type=${parsed.fields.type}`);
  if (parsed.fields.palette) add("frontmatter:palette-value", PALETTES.has(parsed.fields.palette), `palette=${parsed.fields.palette}`);
  if (parsed.fields.rendering) add("frontmatter:rendering-value", RENDERINGS.has(parsed.fields.rendering), `rendering=${parsed.fields.rendering}`);
  if (parsed.fields.text) add("frontmatter:text-value", TEXT_LEVELS.has(parsed.fields.text), `text=${parsed.fields.text}`);
  if (parsed.fields.mood) add("frontmatter:mood-value", MOODS.has(parsed.fields.mood), `mood=${parsed.fields.mood}`);
  if (parsed.fields.font) add("frontmatter:font-value", FONTS.has(parsed.fields.font), `font=${parsed.fields.font}`);
  if (expectedAspect) add("frontmatter:aspect-policy", normalizeAspect(parsed.fields.aspect) === normalizeAspect(expectedAspect), `expected aspect ${expectedAspect}, received ${parsed.fields.aspect || "missing"}`);
  if (expectedText) add("frontmatter:text-policy", parsed.fields.text === expectedText, `expected text=${expectedText}, received ${parsed.fields.text || "missing"}`);

  for (const section of REQUIRED_SECTIONS) {
    add(`section:${section}`, sectionBody(parsed.body, section) !== null, `${section} ${sectionBody(parsed.body, section) === null ? "is missing" : "is present"}`);
  }
  for (const field of REQUIRED_FIELDS) {
    const value = lineValue(parsed.body, field);
    const valid = Boolean(value) && !/^\[[^\]]+\]$/.test(value);
    add(`field:${field}`, valid, `${field} ${valid ? "is populated" : "is missing or still a placeholder"}`);
  }

  const context = sectionBody(parsed.body, "# Content Context") || "";
  const summary = lineValue(context, "Content summary");
  const keywords = lineValue(context, "Keywords");
  add("content-summary-depth", Boolean(summary && summary.length >= 30), "Content summary must contain a concrete multi-sentence summary");
  add("content-keywords", Boolean(keywords && keywords.split(/[,，、]/).filter(Boolean).length >= 3), "Keywords must contain at least three extracted terms");

  if (source) {
    const title = sourceTitle(source);
    add("source-title-context", !title || (context.includes(title) || parsed.body.includes(title)), title ? `source title ${parsed.body.includes(title) ? "is" : "is not"} recorded in the prompt context` : "source has no frontmatter title");
  }

  const textElements = sectionBody(parsed.body, "# Text Elements") || "";
  if (parsed.fields.text === "none") {
    add("text-none-declaration", /no text elements/i.test(textElements), "text=none requires an explicit No text elements declaration");
    add("text-none-no-positive-title", !/\b(?:Title|Subtitle|Tags)\s*:/i.test(textElements), "text=none must not contain positive title, subtitle or tag instructions");
  } else {
    add("text-positive-declaration", /\b(?:Title|Subtitle)\s*:/i.test(textElements), `text=${parsed.fields.text} requires explicit text element instructions`);
  }

  const forbiddenPlaceholders = parsed.body.match(/\[(?:confirmed|2-3 sentence|5-8 key|title or|context|primary, background|key characteristics|type-specific layout)[^\]]*\]/gi) || [];
  add("no-template-placeholders", forbiddenPlaceholders.length === 0, forbiddenPlaceholders.length ? `unresolved template placeholders: ${forbiddenPlaceholders.join(", ")}` : "no unresolved template placeholders");

  return { passed: errors.length === 0, checks, errors, actual: parsed.fields };
}

export async function validateCoverPrompt({ packageDir, promptFile, sourceFile = "formatted.md", expectedAspect = "2.35:1", expectedText, reportFile = "cover/prompt-validation.json" }) {
  const resolvedPackage = resolve(packageDir);
  const resolvedPrompt = resolve(resolvedPackage, promptFile);
  const resolvedSource = resolve(resolvedPackage, sourceFile);
  const resolvedReport = resolve(resolvedPackage, reportFile);
  const report = {
    schema_version: "1.0",
    prompt_file: relativePackagePath(resolvedPackage, promptFile),
    source_file: relativePackagePath(resolvedPackage, sourceFile),
    report_file: relativePackagePath(resolvedPackage, reportFile),
    expected: { aspect: expectedAspect || null, text: expectedText || null },
    passed: false,
    checks: [],
    errors: [],
  };
  let prompt = "";
  let source = "";
  try {
    prompt = await readFile(resolvedPrompt, "utf8");
  } catch (error) {
    report.errors.push(`cannot read prompt: ${error.message}`);
  }
  try {
    source = await readFile(resolvedSource, "utf8");
  } catch (error) {
    report.errors.push(`cannot read source: ${error.message}`);
  }
  if (prompt) {
    const result = validate({ prompt, source, expectedAspect, expectedText });
    report.passed = result.passed && report.errors.length === 0;
    report.checks = result.checks;
    report.errors.push(...result.errors);
    report.actual = result.actual;
  }
  report.generated_at = new Date().toISOString();
  await atomicWrite(resolvedReport, `${JSON.stringify(report, null, 2)}\n`);
  return report;
}

const args = parseArgs(process.argv.slice(2));
try {
  const packageDir = required(args, "package-dir");
  let promptFile = args["prompt-file"];
  if (!promptFile) {
    const entries = await readdir(resolve(packageDir, "cover/prompts"), { withFileTypes: true });
    promptFile = `cover/prompts/${entries.filter((entry) => entry.isFile() && /\.(?:md|txt)$/i.test(entry.name)).map((entry) => entry.name).sort()[0] || ""}`;
  }
  if (!promptFile || promptFile.endsWith("/")) throw new Error("No cover prompt file found");
  const expectedText = args["expected-text"] || (await configuredText(resolve(packageDir))).value || null;
  const report = await validateCoverPrompt({
    packageDir,
    promptFile,
    sourceFile: args["source-file"] || "formatted.md",
    expectedAspect: args["expected-aspect"] || "2.35:1",
    expectedText,
    reportFile: args["report-file"] || "cover/prompt-validation.json",
  });
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
