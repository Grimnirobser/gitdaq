#!/usr/bin/env python3
"""GITDAQ:TOP20 —— GitHub star 数前 20 的仓库，每仓一张 K 线，出 showcase/。

由 .github/workflows/showcase.yml 每日运行；本地跑需已登录 gh CLI。
当日已生成过的图直接复用（SVG 元信息里含渲染日期），失败的仓库保留昨日图。
"""
import html
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "showcase"


def render(item):
    """渲染一个仓库的 SVG；当日已有则跳过。返回是否有图可展示。"""
    full = item["full_name"]
    d = OUT / full.replace("/", "-")
    light = d / "kline-light.svg"
    if light.exists() and date.today().isoformat() in light.read_text():
        return True
    r = subprocess.run(
        [sys.executable, str(ROOT / "kline.py"),
         "--github-repo", full, "--svg", str(d)],
        capture_output=True, text=True)
    if r.returncode:
        print(f"skip {full}: {r.stderr.strip()}", file=sys.stderr)
    return light.exists()  # 失败但有昨日图 → 照常上榜


def main():
    out = subprocess.run(
        ["gh", "api",
         "search/repositories?q=stars:>10000&sort=stars&order=desc&per_page=20"],
        capture_output=True, text=True, check=True).stdout
    top = json.loads(out)["items"]
    OUT.mkdir(exist_ok=True)

    with ThreadPoolExecutor(5) as ex:
        ok = list(ex.map(render, top))
    board = [it for it, o in zip(top, ok) if o]

    keep = {it["full_name"].replace("/", "-") for it in board}
    for d in OUT.iterdir():  # 掉榜仓库的旧图清掉
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d)

    today = date.today().isoformat()
    total = sum(it["stargazers_count"] for it in top)
    lines = [
        "# GITDAQ:TOP20",
        "",
        f"The 20 most-starred repositories on GitHub, each rendered as a "
        f"candlestick chart by [gitdaq](../README.md). Regenerated daily "
        f"by [`showcase.yml`](../.github/workflows/showcase.yml).",
        "",
        f"**Index: ★ {total:,}** (sum of all 20) · close {today} · "
        f"candles = daily commits on the default branch (all authors) · "
        f"volume = lines changed · red up, green down (A-share convention).",
        "",
    ]
    for i, it in enumerate(board, 1):
        full, slug = it["full_name"], it["full_name"].replace("/", "-")
        desc = html.escape(it.get("description") or "", quote=False)
        chip = f" · `{it['language']}`" if it.get("language") else ""
        lines += [
            f"### {i:02d} · [{full}](https://github.com/{full}) — "
            f"★ {it['stargazers_count']:,}",
            "",
            *([f"> {desc}{chip}", ""] if desc or chip else []),
            "<picture>",
            f'  <source media="(prefers-color-scheme: dark)" '
            f'srcset="{slug}/kline-dark.svg">',
            f'  <img alt="{html.escape(full)} daily commits as a candlestick '
            f'chart" src="{slug}/kline-light.svg">',
            "</picture>",
            "",
        ]
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"showcase: {len(board)}/20 charted, index ★ {total:,}")


if __name__ == "__main__":
    main()
