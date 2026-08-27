import { mkdir, rename, stat, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { randomUUID } from "node:crypto";

export function parseArgs(argv) {
  const result = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      result._.push(token);
      continue;
    }
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      result[key] = next;
      i += 1;
    } else {
      result[key] = true;
    }
  }
  return result;
}

export function required(args, name) {
  const value = args[name];
  if (value === undefined || value === "") {
    throw new Error(`Missing --${name}`);
  }
  return value;
}

export async function nonEmpty(file) {
  try {
    const info = await stat(file);
    return info.isFile() && info.size > 0;
  } catch {
    return false;
  }
}

export function packageRelativePath(packageDir, value) {
  const root = resolve(packageDir);
  const target = isAbsolute(value) ? resolve(value) : resolve(root, value);
  const normalized = relative(root, target);
  if (normalized === "" || normalized === ".") {
    throw new Error(`Output must be a file inside package: ${value}`);
  }
  if (normalized.startsWith("..") || isAbsolute(normalized)) {
    throw new Error(`Output must stay inside package: ${value}`);
  }
  return normalized;
}

export function packageFile(packageDir, value) {
  const root = resolve(packageDir);
  const target = isAbsolute(value) ? resolve(value) : resolve(root, value);
  const relativePath = relative(root, target);
  if (!relativePath || relativePath.startsWith("..") || isAbsolute(relativePath)) {
    throw new Error(`Path must stay inside package: ${value}`);
  }
  return target;
}

export function list(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  if (value.trim().startsWith("[")) return JSON.parse(value);
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export async function atomicWrite(file, value) {
  await mkdir(dirname(file), { recursive: true });
  const temporary = `${file}.tmp-${randomUUID()}`;
  await writeFile(temporary, value, "utf8");
  await rename(temporary, file);
}
