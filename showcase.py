#!/usr/bin/env python3
"""GITDAQ-20 指数 —— 对标纳斯达克100：GitHub star 市值前 20 的成分仓库，
每仓一张 K 线。出 showcase/ 明细页，并重写主 README 的行情板标记区。

由 .github/workflows/showcase.yml 每日运行；本地跑需已登录 gh CLI。
当日已生成过的图直接复用（SVG 元信息里含渲染日期），失败的仓库保留昨日图。
"""
import html
import json
import re
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
         "--github-repo", full, "--metric", "stars", "--svg", str(d)],
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
        "# GITDAQ-20",
        "",
        f"What the NASDAQ-100 is to stocks, the **GITDAQ-20** is to GitHub: "
        f"the 20 most-starred repositories on the exchange, each rendered as "
        f"a candlestick chart by [gitdaq](../README.md). Reconstituted daily "
        f"by [`showcase.yml`](../.github/workflows/showcase.yml).",
        "",
        f"**^GDQ20 {total:,}** (sum of constituents' stars) · close {today} · "
        f"candles = **new stars per day** (close vs. yesterday's intake) · "
        f"volume = **new forks per day** · red = hype accelerating, green = "
        f"cooling (A-share convention).",
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
            f'  <img alt="{html.escape(full)} daily new stars as a candlestick '
            f'chart" src="{slug}/kline-light.svg">',
            "</picture>",
            "",
        ]
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")

    update_main_readme(board, total, today)
    print(f"GITDAQ-20: {len(board)}/20 charted, ^GDQ20 {total:,}")


MARK = ("<!-- GDQ20:START -->", "<!-- GDQ20:END -->")


def update_main_readme(board, total, today):
    """重写主 README 标记区：指数点位（对昨收算涨跌）+ 2 列行情板。"""
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    prev = re.search(r"<!-- GDQ20:v(\d+) -->", text)
    tick = ""
    if prev and (p := int(prev.group(1))) and p != total:
        pct = (total - p) / p * 100
        tick = f" {'▲' if total > p else '▼'} {total - p:+,} ({pct:+.2f}%)"

    cells = []
    for i, it in enumerate(board, 1):
        full, slug = it["full_name"], it["full_name"].replace("/", "-")
        cells.append(
            f'<td valign="top" width="50%">\n'
            f'<b>{i:02d} · <a href="https://github.com/{full}">{full}</a></b>'
            f' — ★ {it["stargazers_count"]:,}<br>\n'
            f'<picture>\n'
            f'  <source media="(prefers-color-scheme: dark)" '
            f'srcset="showcase/{slug}/kline-dark.svg">\n'
            f'  <img alt="{html.escape(full)} daily new stars as a candlestick '
            f'chart" src="showcase/{slug}/kline-light.svg">\n'
            f'</picture>\n</td>')
    trs = ["<tr>\n" + "\n".join(cells[i:i + 2]) + "\n</tr>"
           for i in range(0, len(cells), 2)]
    block = "\n".join([
        MARK[0],
        f"<!-- GDQ20:v{total} -->",
        f"**^GDQ20 &nbsp;{total:,}**{tick} · close {today} · "
        f"[constituent detail →](showcase/README.md)",
        "",
        "<table>", *trs, "</table>",
        MARK[1],
    ])
    pat = re.escape(MARK[0]) + r".*?" + re.escape(MARK[1])
    new = re.sub(pat, lambda m: block, text, count=1, flags=re.S)
    if new == text and MARK[0] not in text:
        sys.exit("README.md 缺少 GDQ20 标记区")
    readme.write_text(new, encoding="utf-8")


if __name__ == "__main__":
    main()
