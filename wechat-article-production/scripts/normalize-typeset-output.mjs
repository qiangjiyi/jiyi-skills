#!/usr/bin/env node

// Normalizes the gzh-design-skill output filenames into the package contract
// that wechat-article-production's validators expect.
//
// gzh-design-skill writes (per its SKILL.md step 6):
//   - `{original}_排版_{theme}.html`            (clean body fragment)
//   - `{original}_排版_{theme}_预览.html`       (preview wrapper)
// but validate-package.mjs requires fixed `article.html` and
// `article-preview.html`. This script renames the latest gzh outputs into
// those canonical names, mirroring normalize-format-output.mjs.

import { readdir, readFile, rename, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { parseArgs, required, packageFile, nonEmpty } from "./shared.mjs";

async function sameContent(first, second) {
  try {
    const [a, b] = await Promise.all([readFile(first), readFile(second)]);
    return a.equals(b);
  } catch {
    return false;
  }
}

async function moveDerivedOutput(packageDir, derived, canonical) {
  if (await nonEmpty(canonical)) {
    if (await nonEmpty(derived) && !(await sameContent(canonical, derived))) {
      throw new Error(`Canonical typeset output conflicts with a newer native output: ${canonical}`);
    }
    return "canonical";
  }
  if (!(await nonEmpty(derived))) return null;
  await rename(derived, canonical);
  return "normalized";
}

async function latestMtime(file) {
  try {
    return (await stat(file)).mtimeMs;
  } catch {
    return -Infinity;
  }
}

async function pickLatest(packageDir, candidates) {
  if (candidates.length === 0) return null;
  let best = candidates[0];
  let bestMtime = await latestMtime(resolve(packageDir, best));
  for (const candidate of candidates.slice(1)) {
    const mtime = await latestMtime(resolve(packageDir, candidate));
    if (mtime > bestMtime) {
      best = candidate;
      bestMtime = mtime;
    }
  }
  return best;
}

const args = parseArgs(process.argv.slice(2));
try {
  const packageDir = resolve(required(args, "package-dir"));
  const entries = (await readdir(packageDir)).filter((name) => /\.html$/i.test(name));
  const cleanCandidates = entries.filter((name) => name.includes("_排版_") && !name.endsWith("_预览.html"));
  const previewCandidates = entries.filter((name) => name.endsWith("_预览.html"));

  const cleanSource = await pickLatest(packageDir, cleanCandidates);
  const previewSource = await pickLatest(packageDir, previewCandidates);

  const article = packageFile(packageDir, "article.html");
  const articlePreview = packageFile(packageDir, "article-preview.html");

  const cleanStatus = cleanSource
    ? await moveDerivedOutput(packageDir, packageFile(packageDir, cleanSource), article)
    : null;
  const previewStatus = previewSource
    ? await moveDerivedOutput(packageDir, packageFile(packageDir, previewSource), articlePreview)
    : null;

  const missing = [];
  if (!(await nonEmpty(article))) missing.push("article.html");
  if (!(await nonEmpty(articlePreview))) missing.push("article-preview.html");
  if (missing.length) {
    throw new Error(
      `Typeset outputs are missing: ${missing.join(", ")}; expected gzh-design-skill outputs ` +
      `matching *_排版_*.html and *_预览.html in the package root`,
    );
  }

  console.log(JSON.stringify({
    ok: true,
    package_dir: packageDir,
    sources: { article: cleanSource, article_preview: previewSource },
    canonical_outputs: ["article.html", "article-preview.html"],
    status: { article: cleanStatus, article_preview: previewStatus },
  }));
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
