#!/usr/bin/env python3
"""Publish a WeChat Official Account newspic draft from a source file."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_API_BASE = "https://api.weixin.qq.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TITLE_MAX_CHARS = 20
CONTENT_MAX_CHARS = 1200
MAX_IMAGES = 20


class PublishError(Exception):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish WeChat newspic draft from Markdown/YAML/JSON or direct flags.")
    parser.add_argument("source", nargs="?", help="Source file (.md/.yaml/.yml/.json). Defaults to source.md in the current directory.")
    parser.add_argument("--title", help="Override/provide the draft title.")
    parser.add_argument("--content", help="Override/provide the draft content.")
    parser.add_argument("--image", action="append", metavar="PATH", help="Image path; repeat for multiple images. Replaces images from the source.")
    parser.add_argument("--author", help="Override/provide the author.")
    parser.add_argument("--digest", help="Override/provide the digest.")
    parser.add_argument("--account", help="Account alias from WECHAT_ACCOUNTS.")
    parser.add_argument("--env-file", help="Path to .env/.env.local.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the draft payload without uploading.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    return parser.parse_args()


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env[key] = value
    return env


def merged_env(base_dir: Path, env_file: str | None) -> tuple[dict[str, str], Path | None]:
    candidates = []
    if env_file:
        candidates.append(Path(env_file).expanduser())
    else:
        # Look beside the source first, then fall back to the skill directory so
        # credentials can live next to the skill instead of every source folder.
        skill_dir = Path(__file__).resolve().parent.parent
        candidates.extend([
            base_dir / ".env.local",
            base_dir / ".env",
            skill_dir / ".env.local",
            skill_dir / ".env",
        ])
    file_env: dict[str, str] = {}
    used: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            file_env = load_dotenv(candidate)
            used = candidate
            break
    env = dict(os.environ)
    env.update(file_env)
    return env, used


def parse_scalar(value: str):
    value = value.strip()
    if value in ("", "null", "Null", "NULL", "~"):
        return None
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def parse_simple_yaml(text: str) -> dict:
    data: dict[str, object] = {}
    current_list: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith(" ") or raw_line.startswith("\t"):
            if current_list and raw_line.strip().startswith("- "):
                item = raw_line.strip()[2:].strip()
                data.setdefault(current_list, [])
                assert isinstance(data[current_list], list)
                data[current_list].append(parse_scalar(item))
                continue
            raise PublishError(f"unsupported YAML line: {raw_line}")
        if ":" not in raw_line:
            raise PublishError(f"unsupported YAML line: {raw_line}")
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            data[key] = []
            current_list = key
        else:
            data[key] = parse_scalar(value)
            current_list = None
    return data


def split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return "", text


def parse_markdown(text: str) -> dict:
    frontmatter, body = split_frontmatter(text)
    data = parse_simple_yaml(frontmatter) if frontmatter.strip() else {}
    body_lines = body.splitlines()
    title = None
    content_start = 0
    for idx, line in enumerate(body_lines):
        if not line.strip():
            continue
        if line.lstrip().startswith("# "):
            title = line.lstrip()[2:].strip()
            content_start = idx + 1
        else:
            content_start = idx
        break
    content = "\n".join(body_lines[content_start:]).strip()
    if title and not data.get("title"):
        data["title"] = title
    if content and not data.get("content"):
        data["content"] = content
    return data


def load_source(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        data = parse_simple_yaml(text)
    elif suffix in (".md", ".markdown"):
        data = parse_markdown(text)
    else:
        raise PublishError("source must be .md, .yaml, .yml, or .json")
    if not isinstance(data, dict):
        raise PublishError("source must contain an object at the top level")
    return data


def env_key(account: str, suffix: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", account).strip("_").upper()
    return f"WECHAT_{normalized}_{suffix}"


def configured_accounts(env: dict[str, str]) -> list[str]:
    raw = env.get("WECHAT_ACCOUNTS", "")
    accounts = [x.strip() for x in raw.split(",") if x.strip()]
    if accounts:
        return accounts
    if env.get("WECHAT_APP_ID") or env.get("WECHAT_ACCESS_TOKEN"):
        return ["default"]
    return []


def resolve_account(cli_account: str | None, source: dict, env: dict[str, str]) -> str:
    if cli_account:
        return cli_account
    source_account = source.get("account")
    if isinstance(source_account, str) and source_account.strip():
        return source_account.strip()
    accounts = configured_accounts(env)
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise PublishError("no WeChat account configured in env")
    raise PublishError(f"multiple accounts configured ({', '.join(accounts)}); set source account or --account")


def account_value(env: dict[str, str], account: str, suffix: str) -> str:
    if account != "default":
        value = env.get(env_key(account, suffix), "")
        if value:
            return value
    return env.get(f"WECHAT_{suffix}", "")


def validate_source(source: dict, base_dir: Path) -> tuple[str, str, str, str, list[Path]]:
    title = str(source.get("title") or "").strip()
    content = str(source.get("content") or "").strip()
    author = str(source.get("author") or "").strip()
    digest = str(source.get("digest") or "").strip()
    images_raw = source.get("images")
    if not title:
        raise PublishError("title is required")
    if len(title) > TITLE_MAX_CHARS:
        raise PublishError(f"title must be at most {TITLE_MAX_CHARS} characters, got {len(title)}")
    if not content:
        raise PublishError("content is required")
    if not isinstance(images_raw, list):
        raise PublishError("images must be a list")
    if not 1 <= len(images_raw) <= MAX_IMAGES:
        raise PublishError(f"images must contain 1-{MAX_IMAGES} paths")
    images: list[Path] = []
    for item in images_raw:
        p = Path(str(item)).expanduser()
        if not p.is_absolute():
            p = base_dir / p
        if not p.exists() or not p.is_file():
            raise PublishError(f"image not found: {p}")
        images.append(p)
    return title, content[:CONTENT_MAX_CHARS], author, digest, images


def request_json(url: str, method: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raw = err.read()
        raise PublishError(f"HTTP {err.code}: {raw.decode('utf-8', errors='replace')}") from err
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict) and data.get("errcode"):
        raise PublishError(f"WeChat error {data.get('errcode')}: {data.get('errmsg')}")
    return data


def proxy_json(proxy_url: str, url: str, method: str, payload: dict | None = None) -> dict:
    envelope = {"url": url, "method": method}
    if payload is not None:
        envelope["data"] = payload
    return request_json(proxy_url, "POST", envelope)


def get_access_token(env: dict[str, str], account: str, api_base: str, proxy_url: str) -> str:
    direct_token = account_value(env, account, "ACCESS_TOKEN")
    if direct_token:
        return direct_token
    app_id = account_value(env, account, "APP_ID")
    app_secret = account_value(env, account, "APP_SECRET")
    if not app_id or not app_secret:
        raise PublishError(f"missing app id/secret for account: {account}")
    query = urllib.parse.urlencode({
        "grant_type": "client_credential",
        "appid": app_id,
        "secret": app_secret,
    })
    url = f"{api_base}/cgi-bin/token?{query}"
    data = proxy_json(proxy_url, url, "GET") if proxy_url else request_json(url, "GET")
    token = data.get("access_token")
    if not token:
        raise PublishError(f"access_token missing in response: {data}")
    return str(token)


def with_token(url: str, token: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["access_token"] = [token]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def upload_image_direct(api_base: str, token: str, image: Path) -> str:
    boundary = "----wechat-newspic-boundary"
    mime_type = mimetypes.guess_type(str(image))[0] or "image/jpeg"
    file_data = image.read_bytes()
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="media"; filename="{image.name}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        file_data,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    query = urllib.parse.urlencode({"type": "image"})
    url = with_token(f"{api_base}/cgi-bin/material/add_material?{query}", token)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errcode"):
        raise PublishError(f"WeChat error {data.get('errcode')}: {data.get('errmsg')}")
    media_id = data.get("media_id")
    if not media_id:
        raise PublishError(f"media_id missing for {image}: {data}")
    return str(media_id)


def upload_image_proxy(proxy_url: str, api_base: str, token: str, image: Path) -> str:
    query = urllib.parse.urlencode({"type": "image"})
    url = with_token(f"{api_base}/cgi-bin/material/add_material?{query}", token)
    mime_type = mimetypes.guess_type(str(image))[0] or "image/jpeg"
    payload = {
        "url": url,
        "method": "UPLOAD",
        "fileData": base64.b64encode(image.read_bytes()).decode("ascii"),
        "fileName": image.name,
        "mimeType": mime_type,
        "fieldName": "media",
    }
    data = request_json(proxy_url, "POST", payload)
    media_id = data.get("media_id")
    if not media_id:
        raise PublishError(f"media_id missing for {image}: {data}")
    return str(media_id)


def add_newspic_draft(api_base: str, proxy_url: str, token: str, title: str, content: str, author: str, digest: str, media_ids: list[str]) -> str:
    item = {
        "article_type": "newspic",
        "title": title,
        "content": content,
        "image_info": {
            "image_list": [{"image_media_id": media_id} for media_id in media_ids],
        },
    }
    if author:
        item["author"] = author
    if digest:
        item["digest"] = digest
    payload = {"articles": [item]}
    url = with_token(f"{api_base}/cgi-bin/draft/add", token)
    data = proxy_json(proxy_url, url, "POST", payload) if proxy_url else request_json(url, "POST", payload)
    media_id = data.get("media_id")
    if not media_id:
        raise PublishError(f"draft media_id missing: {data}")
    return str(media_id)


def confirm(account: str, title: str, content: str, images: list[Path]) -> None:
    print("Ready to publish WeChat newspic draft:")
    print(f"  account: {account}")
    print(f"  title: {title}")
    print(f"  content: {content}")
    print(f"  images: {len(images)}")
    answer = input("Publish now? Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise PublishError("cancelled")


def main() -> int:
    args = parse_args()
    try:
        direct = args.title is not None or args.content is not None or bool(args.image)
        if args.source:
            source_path = Path(args.source).expanduser().resolve()
            source = load_source(source_path)
            base_dir = source_path.parent
        elif direct:
            source = {}
            base_dir = Path.cwd()
        else:
            source_path = (Path.cwd() / "source.md").resolve()
            if not source_path.exists():
                raise PublishError("no source given and source.md not found in current directory")
            source = load_source(source_path)
            base_dir = source_path.parent
        if args.title is not None:
            source["title"] = args.title
        if args.content is not None:
            source["content"] = args.content
        if args.author is not None:
            source["author"] = args.author
        if args.digest is not None:
            source["digest"] = args.digest
        if args.image:
            source["images"] = list(args.image)
        env, used_env = merged_env(base_dir, args.env_file)
        account = resolve_account(args.account, source, env)
        title, content, author, digest, images = validate_source(source, base_dir)
        api_base = env.get("WECHAT_API_BASE") or DEFAULT_API_BASE
        proxy_url = env.get("WECHAT_PROXY_URL", "")
        if args.dry_run:
            print(json.dumps({
                "account": account,
                "env_file": str(used_env) if used_env else None,
                "api_base": api_base,
                "proxy": bool(proxy_url),
                "draft": {
                    "article_type": "newspic",
                    "title": title,
                    "author": author,
                    "digest": digest,
                    "content": content,
                    "images": [str(p) for p in images],
                },
            }, ensure_ascii=False, indent=2))
            return 0
        if not args.yes:
            confirm(account, title, content, images)
        token = get_access_token(env, account, api_base, proxy_url)
        media_ids = []
        for i, image in enumerate(images, start=1):
            print(f"[{i}/{len(images)}] upload {image}")
            media_id = upload_image_proxy(proxy_url, api_base, token, image) if proxy_url else upload_image_direct(api_base, token, image)
            media_ids.append(media_id)
        draft_media_id = add_newspic_draft(api_base, proxy_url, token, title, content, author, digest, media_ids)
        print(json.dumps({"ok": True, "account": account, "draft_media_id": draft_media_id}, ensure_ascii=False))
        return 0
    except (PublishError, OSError, json.JSONDecodeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
