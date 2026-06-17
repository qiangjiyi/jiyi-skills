#!/usr/bin/env node
// Thin wrapper around baoyu-md: read a markdown file, render to themed HTML,
// and emit a JSON payload on stdout that the Python publish.py can consume.
//
// Usage:
//   node render_markdown.mjs \
//     --markdown <path> \
//     [--theme default|grace|simple|modern] \
//     [--color blue|green|...|#hex] \
//     [--no-cite]
//
// Stdout JSON shape:
//   {
//     "title": "...",
//     "author": "...",
//     "summary": "...",
//     "html": "...",
//     "coverHint": "<absolute path or null>",   // frontmatter coverImage/featureImage/cover/image
//     "contentImages": [
//       { "placeholder": "WECHATIMGPH_1", "originalPath": "...", "localPath": "<abs>", "alt": "..." },
//       ...
//     ]
//   }
//
// On error, exits non-zero and writes `{"error": "..."}` to stderr is avoided;
// instead, write the error to stderr and exit 1 so Python can capture both streams.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import {
  COLOR_PRESETS,
  THEME_NAMES,
  cleanSummaryText,
  extractSummaryFromBody,
  extractTitleFromMarkdown,
  parseFrontmatter,
  renderMarkdownDocument,
  replaceMarkdownImagesWithPlaceholders,
  resolveColorToken,
  resolveContentImages,
  stripWrappingQuotes,
} from "baoyu-md";

const VALID_THEMES = new Set(THEME_NAMES);
const VALID_COLOR_NAMES = new Set(Object.keys(COLOR_PRESETS));

function parseArgs(argv) {
  const args = { markdown: null, theme: null, color: null, citeStatus: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--markdown" && argv[i + 1]) {
      args.markdown = argv[++i];
    } else if (a === "--theme" && argv[i + 1]) {
      args.theme = argv[++i];
    } else if (a === "--color" && argv[i + 1]) {
      args.color = argv[++i];
    } else if (a === "--no-cite") {
      args.citeStatus = false;
    } else if (a === "--cite") {
      args.citeStatus = true;
    } else if (a === "-h" || a === "--help") {
      printUsage();
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${a}`);
    }
  }
  if (!args.markdown) throw new Error("--markdown <path> is required");
  return args;
}

function printUsage() {
  process.stderr.write(
    [
      "Usage: render_markdown.mjs --markdown <path> [options]",
      "",
      "Options:",
      "  --theme <name>       One of: " + [...VALID_THEMES].join(", "),
      "  --color <name|hex>   Preset: " + [...VALID_COLOR_NAMES].join(", ") + " (or #rrggbb)",
      "  --cite / --no-cite   Convert external links to bottom citations (default: --cite)",
      "",
    ].join("\n"),
  );
}

function resolveCoverHint(frontmatter) {
  for (const key of ["coverImage", "featureImage", "cover", "image"]) {
    const v = stripWrappingQuotes(frontmatter[key] ?? "");
    if (v) return v;
  }
  return null;
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`error: ${err.message}\n`);
    process.exit(2);
  }

  if (args.theme && !VALID_THEMES.has(args.theme)) {
    process.stderr.write(`error: invalid --theme '${args.theme}', valid: ${[...VALID_THEMES].join(", ")}\n`);
    process.exit(2);
  }
  if (args.color && !VALID_COLOR_NAMES.has(args.color) && !/^#[0-9a-fA-F]{3,8}$/.test(args.color)) {
    process.stderr.write(
      `error: invalid --color '${args.color}', valid presets: ${[...VALID_COLOR_NAMES].join(", ")} or #hex\n`,
    );
    process.exit(2);
  }

  const markdownPath = path.resolve(args.markdown);
  if (!fs.existsSync(markdownPath)) {
    process.stderr.write(`error: markdown file not found: ${markdownPath}\n`);
    process.exit(2);
  }
  const baseDir = path.dirname(markdownPath);
  const content = fs.readFileSync(markdownPath, "utf-8");

  const { frontmatter, body } = parseFrontmatter(content);

  let title = stripWrappingQuotes(frontmatter.title ?? "") || extractTitleFromMarkdown(body);
  if (!title) title = path.basename(markdownPath, path.extname(markdownPath));

  const author = stripWrappingQuotes(frontmatter.author ?? "");
  const frontmatterSummary =
    stripWrappingQuotes(frontmatter.description ?? "") || stripWrappingQuotes(frontmatter.summary ?? "");
  const summary = cleanSummaryText(frontmatterSummary) || extractSummaryFromBody(body, 120);

  const { images, markdown: rewrittenBody } = replaceMarkdownImagesWithPlaceholders(
    body,
    "WECHATIMGPH_",
  );
  const rewrittenMarkdown =
    (frontmatter && Object.keys(frontmatter).length
      ? `---\n${Object.entries(frontmatter)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\n")}\n---\n`
      : "") + rewrittenBody;

  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "wechat-publisher-render-"));
  const contentImages = await resolveContentImages(images, baseDir, tempDir, "render_markdown");

  const { html } = await renderMarkdownDocument(rewrittenMarkdown, {
    citeStatus: args.citeStatus,
    defaultTitle: title,
    keepTitle: false,
    primaryColor: resolveColorToken(args.color),
    theme: args.theme ?? "default",
  });

  const out = {
    title,
    author,
    summary,
    html,
    coverHint: resolveCoverHint(frontmatter),
    contentImages: contentImages.map((img) => ({
      placeholder: img.placeholder,
      originalPath: img.originalPath,
      localPath: img.localPath,
      alt: img.alt ?? null,
    })),
  };
  process.stdout.write(JSON.stringify(out));
}

main().catch((err) => {
  process.stderr.write(`error: ${err && err.stack ? err.stack : String(err)}\n`);
  process.exit(1);
});
