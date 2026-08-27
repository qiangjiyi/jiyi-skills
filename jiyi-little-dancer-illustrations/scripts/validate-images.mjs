#!/usr/bin/env node

import { mkdir, readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { randomUUID } from "node:crypto";

const ASPECT = 16 / 9;
const ASPECT_TOLERANCE = 0.04;
const IMAGE_PATTERN = /^\d{2}-.+\.(?:png|jpe?g|webp)$/i;

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

function pngDimensions(buffer) {
  if (buffer.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") return null;
  if (buffer.toString("ascii", 12, 16) !== "IHDR") return null;
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  if (!width || !height) return null;
  return { width, height };
}

function relativePackagePath(packageDir, file) {
  const normalized = relative(resolve(packageDir), resolve(packageDir, file));
  if (!normalized || normalized.startsWith("..")) throw new Error(`Path is outside package: ${file}`);
  return normalized;
}

async function atomicWrite(file, content) {
  await mkdir(dirname(file), { recursive: true });
  const temporary = `${file}.tmp-${randomUUID()}`;
  await writeFile(temporary, content, "utf8");
  await rename(temporary, file);
}

/**
 * Validate illustration metadata without loading raster contents into an
 * Agent context. Only the file header, dimensions and byte size are inspected.
 */
export async function inspectImageSet({ packageDir, expectedCount, reportFile = "illustrations/image-validation.json" }) {
  const resolvedPackage = resolve(packageDir);
  const resolvedReport = resolve(resolvedPackage, reportFile);
  const reportPath = relativePackagePath(resolvedPackage, reportFile);
  const errors = [];
  let entries = [];
  try {
    entries = await readdir(join(resolvedPackage, "illustrations"), { withFileTypes: true });
  } catch (error) {
    errors.push(`missing illustrations directory: ${error.message}`);
  }

  const files = entries
    .filter((entry) => entry.isFile() && entry.name !== "ip-reference.png" && IMAGE_PATTERN.test(entry.name))
    .map((entry) => entry.name)
    .sort();

  if (Number.isInteger(expectedCount) && files.length !== expectedCount) {
    errors.push(`expected ${expectedCount} generated image(s), found ${files.length}`);
  }
  if (files.length === 0) errors.push("no generated illustration found");

  const images = [];
  for (let index = 0; index < files.length; index += 1) {
    const name = files[index];
    const relativePath = `illustrations/${name}`;
    const expectedPrefix = `${String(index + 1).padStart(2, "0")}-`;
    const image = { file: relativePath, name, passed: false };
    if (!name.startsWith(expectedPrefix)) {
      image.error = `image order must start with ${expectedPrefix}`;
      errors.push(`${relativePath}: ${image.error}`);
      images.push(image);
      continue;
    }
    try {
      const file = resolve(resolvedPackage, relativePath);
      const info = await stat(file);
      image.bytes = info.size;
      const content = await readFile(file);
      const dimensions = pngDimensions(content);
      if (!dimensions) {
        image.error = "generated illustration must be a readable PNG";
      } else {
        image.width = dimensions.width;
        image.height = dimensions.height;
        image.aspect = Number((dimensions.width / dimensions.height).toFixed(6));
        if (Math.abs(dimensions.width / dimensions.height - ASPECT) > ASPECT_TOLERANCE) {
          image.error = `expected 16:9 image, got ${dimensions.width}:${dimensions.height}`;
        } else {
          image.passed = true;
        }
      }
      if (image.error) errors.push(`${relativePath}: ${image.error}`);
    } catch (error) {
      image.error = error.message;
      errors.push(`${relativePath}: ${image.error}`);
    }
    images.push(image);
  }

  const report = {
    schema_version: "1.0",
    generated_at: new Date().toISOString(),
    report_file: reportPath,
    expected_count: Number.isInteger(expectedCount) ? expectedCount : null,
    count: files.length,
    passed: errors.length === 0 && images.length > 0 && images.every((image) => image.passed === true),
    checks: [
      "generated image count",
      "sequential image names",
      "PNG signature and IHDR",
      "16:9 aspect ratio",
    ],
    images,
    errors,
  };
  await atomicWrite(resolvedReport, `${JSON.stringify(report, null, 2)}\n`);
  return report;
}

const args = parseArgs(process.argv.slice(2));
try {
  const packageDir = required(args, "package-dir");
  const expectedCount = args["expected-count"] === undefined ? null : Number(args["expected-count"]);
  if (expectedCount !== null && (!Number.isInteger(expectedCount) || expectedCount < 1)) {
    throw new Error("--expected-count must be a positive integer");
  }
  const report = await inspectImageSet({
    packageDir,
    expectedCount,
    reportFile: args["report-file"] || "illustrations/image-validation.json",
  });
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
