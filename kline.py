#!/usr/bin/env python3
"""git-kline: 把 git 仓库的每日文件修改事项数量画成股票日K线图（自包含 HTML）。

指标定义（诚实的 OHLC，不编造影线）:
  价格 = 滚动 24 小时内的文件修改事项数（一次提交中改动一个文件 = 1 项）
  开盘 = 当日 0 点的窗口值（数学上恰好等于昨日全天总量）
  收盘 = 当日 24 点的窗口值（恰好等于当日全天总量）
  高/低 = 该窗口值在日内的极值（提交涌入时冲高，旧提交滚出窗口时回落）
  只有发生提交的日子才算“交易日”，空窗期如同休市，重启即跳空。
"""
import argparse
import html
import json
import math
import subprocess
import sys
import time
import webbrowser
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path


def git_commits(repo):
    """返回按时间升序的 [(unix_ts, 修改文件数)]，使用 committer date。"""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--no-merges",
         "--pretty=format:@%ct", "--name-only"],
        capture_output=True, text=True, check=True, errors="replace",
    ).stdout
    commits = []
    # ponytail: 行级解析足够——git 对怪异文件名会整行引号输出，误差为 0
    for line in out.splitlines():
        if line.startswith("@") and line[1:].isdigit():
            commits.append([int(line[1:]), 0])
        elif line.strip() and commits:
            commits[-1][1] += 1
    return sorted((t, k) for t, k in commits if k > 0)


def daily_ohlc(commits):
    """扫描线算法：+k 事件在提交时刻，-k 事件在 24h 后，级别即窗口值。"""
    events = sorted(
        [(t, k) for t, k in commits] + [(t + 86400, -k) for t, k in commits]
    )
    vol = {}
    for t, _ in commits:
        d = datetime.fromtimestamp(t).date()
        vol[d] = vol.get(d, 0) + 1
    rows, level, i = [], 0, 0
    for day in sorted(vol):
        # ponytail: 本地时区午夜做日界，DST 地区一年两天有 ±1h 偏差，可忽略
        start = datetime.combine(day, dtime()).timestamp()
        end = datetime.combine(day + timedelta(days=1), dtime()).timestamp()
        while i < len(events) and events[i][0] < start:
            level += events[i][1]
            i += 1
        o = hi = lo = level
        while i < len(events) and events[i][0] < end:
            level += events[i][1]
            hi = max(hi, level)
            lo = min(lo, level)
            i += 1
        rows.append({"d": day.isoformat(), "o": o, "h": hi, "l": lo,
                     "c": level, "v": vol[day]})
    return rows


def add_ma(rows, n, key, field="c"):
    for i, r in enumerate(rows):
        r[key] = (round(sum(x[field] for x in rows[i - n + 1:i + 1]) / n, 1)
                  if i >= n - 1 else None)


def add_boll(rows, n=20, k=2):
    """布林带 BOLL(n,k)：中轨=SMA(n)，上下轨=±k·σ（总体标准差）。"""
    for i, r in enumerate(rows):
        if i < n - 1:
            r["bu"] = r["bm"] = r["bl"] = None
            continue
        win = [x["c"] for x in rows[i - n + 1:i + 1]]
        m = sum(win) / n
        sd = (sum((v - m) ** 2 for v in win) / n) ** 0.5
        r["bm"] = round(m, 2)
        r["bu"] = round(m + k * sd, 2)
        r["bl"] = round(max(0.0, m - k * sd), 2)  # 计数不为负，下轨贴地即可




def github_daily(user):
    """GitHub 贡献日历（个人主页绿格子的同款数据）→ 升序 [(iso日期, 次数)]。

    经已登录的 gh CLI 走 GraphQL，逐年抓取（API 单次最多一年）。"""
    def gh(*a):
        return subprocess.run(["gh", *a], capture_output=True, text=True,
                              check=True).stdout
    created = int(json.loads(gh("api", f"users/{user}"))["created_at"][:4])
    q = ("query($login:String!,$from:DateTime!,$to:DateTime!){"
         "user(login:$login){contributionsCollection(from:$from,to:$to){"
         "contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}")
    days = {}
    for year in range(created, datetime.now().year + 1):
        out = json.loads(gh("api", "graphql", "-f", f"query={q}",
                            "-f", f"login={user}",
                            "-f", f"from={year}-01-01T00:00:00Z",
                            "-f", f"to={year}-12-31T23:59:59Z"))
        cal = out["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        for w in cal["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
    return sorted(days.items())


def daily_rows(daily):
    """日粒度计数 → K线行。开=昨日、收=今日；日历无日内数据，故无影线。"""
    counts = dict(daily)
    rows = []
    for ds, c in daily:
        if c == 0:
            continue
        prev = counts.get(
            (date.fromisoformat(ds) - timedelta(days=1)).isoformat(), 0)
        rows.append({"d": ds, "o": prev, "h": max(prev, c),
                     "l": min(prev, c), "c": c, "v": c})
    return rows


def github_commit_daily(user, back_to):
    """每日 commit 数（GitHub 贡献口径）。从今天按季度往回抓到 back_to。

    季度窗口 ≤92 天，contributions(first:92) 永不截断，无需分页。
    返回 ({iso日期: commit数}, 覆盖起始日)。"""
    def gh(*a):
        return subprocess.run(["gh", *a], capture_output=True, text=True,
                              check=True).stdout
    q = ("query($login:String!,$from:DateTime!,$to:DateTime!){"
         "user(login:$login){contributionsCollection(from:$from,to:$to){"
         "commitContributionsByRepository(maxRepositories:100){"
         "contributions(first:92){nodes{occurredAt commitCount}}}}}}")
    counts, start = {}, None
    end = date.today()
    cap = 40  # ponytail: 最多回溯约10年，防 GraphQL 配额爆炸；更久远的历史不画
    while cap and end >= back_to:
        begin = max(end - timedelta(days=91), back_to)
        out = json.loads(gh("api", "graphql", "-f", f"query={q}",
                            "-f", f"login={user}",
                            "-f", f"from={begin.isoformat()}T00:00:00Z",
                            "-f", f"to={end.isoformat()}T23:59:59Z"))
        col = out["data"]["user"]["contributionsCollection"]
        for repo in col["commitContributionsByRepository"]:
            for nd in repo["contributions"]["nodes"]:
                ds = nd["occurredAt"][:10]
                counts[ds] = counts.get(ds, 0) + nd["commitCount"]
        start = begin
        end = begin - timedelta(days=1)
        cap -= 1
    return counts, (start or end).isoformat()


def commit_rows(cal, commits, cov_start):
    """蜡烛 = 每日 commit 数，量柱 = 当日总贡献。活跃日 = 贡献>0 且在覆盖期内。"""
    rows = []
    for ds, contrib in cal:
        if contrib <= 0 or ds < cov_start:
            continue
        c = commits.get(ds, 0)
        prev = (date.fromisoformat(ds) - timedelta(days=1)).isoformat()
        # ponytail: 覆盖期第一天的 o 可能查不到昨日 commit，按 0 处理；窗口裁剪后不可见
        o = commits.get(prev, 0)
        rows.append({"d": ds, "o": o, "h": max(o, c), "l": min(o, c),
                     "c": c, "v": contrib})
    return rows


def github_line_daily(user, back_to):
    """每日代码变更行数：用户名下 commit 的 additions+deletions（默认分支）。

    先按季度切片找出窗口内提交过的仓库，再逐仓库分页扫 history。
    返回 ({iso日期: 行数}, 覆盖起始日)。"""
    def gh(*a):
        return subprocess.run(["gh", *a], capture_output=True, text=True,
                              check=True).stdout
    qr = ("query($login:String!,$from:DateTime!,$to:DateTime!){"
          "user(login:$login){contributionsCollection(from:$from,to:$to){"
          "commitContributionsByRepository(maxRepositories:100){"
          "repository{nameWithOwner}}}}}")
    repos, start = set(), None
    end = date.today()
    cap = 40  # ponytail: 与 commit 模式相同的 ~10 年回溯上限
    while cap and end >= back_to:
        begin = max(end - timedelta(days=91), back_to)
        out = json.loads(gh("api", "graphql", "-f", f"query={qr}",
                            "-f", f"login={user}",
                            "-f", f"from={begin.isoformat()}T00:00:00Z",
                            "-f", f"to={end.isoformat()}T23:59:59Z"))
        col = out["data"]["user"]["contributionsCollection"]
        for r in col["commitContributionsByRepository"]:
            repos.add(r["repository"]["nameWithOwner"])
        start = begin
        end = begin - timedelta(days=1)
        cap -= 1
    start = start or end
    uid = json.loads(gh("api", f"users/{user}"))["node_id"]
    qh = ("query($owner:String!,$name:String!,$uid:ID!,"
          "$since:GitTimestamp!,$cursor:String){"
          "repository(owner:$owner,name:$name){defaultBranchRef{target{"
          "... on Commit{history(author:{id:$uid},since:$since,"
          "first:100,after:$cursor){pageInfo{hasNextPage endCursor}"
          "nodes{committedDate additions deletions}}}}}}}")
    lines = {}
    for full in sorted(repos):
        owner, name = full.split("/", 1)
        cursor = None
        for _ in range(10):  # ponytail: 每仓库窗口内最多扫 1000 个 commit
            argv = ["api", "graphql", "-f", f"query={qh}",
                    "-f", f"owner={owner}", "-f", f"name={name}",
                    "-f", f"uid={uid}",
                    "-f", f"since={start.isoformat()}T00:00:00Z"]
            if cursor:
                argv += ["-f", f"cursor={cursor}"]
            try:
                out = json.loads(gh(*argv))
            except subprocess.CalledProcessError:
                break  # 仓库被删/无权限：跳过，不让整次生成失败
            ref = (out.get("data") or {}).get("repository") or {}
            tgt = (ref.get("defaultBranchRef") or {}).get("target") or {}
            hist = tgt.get("history")
            if not hist:
                break
            for nd in hist["nodes"]:
                ds = nd["committedDate"][:10]
                lines[ds] = (lines.get(ds, 0)
                             + (nd["additions"] or 0) + (nd["deletions"] or 0))
            if not hist["pageInfo"]["hasNextPage"]:
                break
            cursor = hist["pageInfo"]["endCursor"]
    return lines, start.isoformat()


def github_repo_daily(full, need_days):
    """仓库维度：默认分支全体作者的每日 commit 数与变更行数。

    从最新分页往回抓，凑够 need_days 个活跃日即停（0=尽量全量），上限 100 页。
    返回 (每日commit数, 每日行数, 覆盖起始日)。"""
    def gh(*a):
        return subprocess.run(["gh", *a], capture_output=True, text=True,
                              check=True).stdout
    owner, name = full.split("/", 1)
    q = ("query($owner:String!,$name:String!,$cursor:String){"
         "repository(owner:$owner,name:$name){defaultBranchRef{target{"
         "... on Commit{history(first:100,after:$cursor){"
         "pageInfo{hasNextPage endCursor}"
         "nodes{committedDate additions deletions}}}}}}}")
    cmap, lmap = {}, {}
    cursor, more = None, False
    for _ in range(100):  # ponytail: 最多扫 1 万个 commit，巨型仓库画不到更早就截断
        argv = ["api", "graphql", "-f", f"query={q}",
                "-f", f"owner={owner}", "-f", f"name={name}"]
        if cursor:
            argv += ["-f", f"cursor={cursor}"]
        out = json.loads(gh(*argv))
        ref = (out.get("data") or {}).get("repository") or {}
        tgt = (ref.get("defaultBranchRef") or {}).get("target") or {}
        hist = tgt.get("history")
        if not hist:
            break
        for nd in hist["nodes"]:
            ds = nd["committedDate"][:10]
            cmap[ds] = cmap.get(ds, 0) + 1
            lmap[ds] = (lmap.get(ds, 0)
                        + (nd["additions"] or 0) + (nd["deletions"] or 0))
        more = hist["pageInfo"]["hasNextPage"]
        if not more or (need_days and len(cmap) > need_days):
            break
        cursor = hist["pageInfo"]["endCursor"]
    if not cmap:
        return {}, {}, None
    days = sorted(cmap)
    # 提前停止时最老一天可能只覆盖了半天 → 丢弃；自然翻完则完整保留
    cov = days[0] if not more or len(days) == 1 else days[1]
    return cmap, lmap, cov


STAR_PAGE_CAP = 60    # ponytail: 回填只管开局宽度；巨仓库深分页被限流，翻多少算多少，
FORK_PAGE_CAP = 30    # 之后每日差分自动加宽。fork 流量约为 star 的 5~10%


def github_repo_stars_daily(full, need_days, cache_file):
    """仓库维度·人气口径：每日新增 star（蜡烛）与新增 fork（量柱），JSON 缓存。

    日常模式（缓存已有总量基线）：只查 stargazerCount/forkCount，与上次
    基线做差分记到今天——一次查询零分页，CI 里绝对稳。差分是净增
    （加星减取消），负增之日记 0 视同休市。
    回填模式（首次）：分页翻历史事件建近段日史。stargazer 深分页会被
    GitHub 二级限流打断（页间限速+耐心重试仍可能到不了底），翻到哪里
    floor 就定在哪里，如实截断，之后靠每日差分自然延展。回填当日只有
    半日事件，次日差分补齐。"""
    def gh(*a, waits=(2, 5, 20, 60)):
        for wait in (*waits, 0):
            r = subprocess.run(["gh", *a], capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout
            if not wait:
                raise subprocess.CalledProcessError(r.returncode, r.args,
                                                    r.stdout, r.stderr)
            time.sleep(wait)
        raise AssertionError  # unreachable

    owner, name = full.split("/", 1)
    cache = (json.loads(cache_file.read_text())
             if cache_file.exists() else {"stars": {}, "forks": {}})
    today = date.today().isoformat()
    var = ["-f", f"owner={owner}", "-f", f"name={name}"]

    def totals():
        out = json.loads(gh(
            "api", "graphql", "-f",
            "query=query($owner:String!,$name:String!){repository("
            "owner:$owner,name:$name){stargazerCount forkCount}}", *var))
        repo = (out.get("data") or {}).get("repository")
        return (repo["stargazerCount"], repo["forkCount"]) if repo else None

    if "total_stars" in cache:            # 日常：总量差分
        t = totals()
        if t:
            for key, tot, new in (("stars", "total_stars", t[0]),
                                  ("forks", "total_forks", t[1])):
                gain = max(0, new - cache[tot])
                if gain:                  # 距上次观测的净增全记到今天
                    cache[key][today] = cache[key].get(today, 0) + gain
                cache[tot] = new
    else:                                 # 首次：分页回填
        def walk(conn, field, sel, key, counts, cap, stop_date, need=0):
            """从最新事件往回按日计数；返回是否翻尽（到达仓库诞生）。"""
            q = ("query($owner:String!,$name:String!,$cursor:String){"
                 "repository(owner:$owner,name:$name){"
                 + conn + "(first:100,after:$cursor,orderBy:{field:" + field
                 + ",direction:DESC}){pageInfo{hasNextPage endCursor}"
                 + sel + "}}}")
            cursor = None
            for _ in range(cap):
                argv = ["api", "graphql", "-f", f"query={q}", *var]
                if cursor:
                    argv += ["-f", f"cursor={cursor}"]
                try:                      # 分页快败：截断优于苦等
                    out = json.loads(gh(*argv, waits=(2, 5)))
                except subprocess.CalledProcessError:
                    return False          # 限流打不穿 → 截断到此
                page = ((out.get("data") or {}).get("repository")
                        or {}).get(conn)
                if not page:
                    return False
                for it in (page.get("edges") or page.get("nodes") or []):
                    ds = it[key][:10]
                    if stop_date and ds < stop_date:
                        return False      # 已够旧
                    counts[ds] = counts.get(ds, 0) + 1
                if not page["pageInfo"]["hasNextPage"]:
                    return True
                if need and len(counts) > need:
                    return False
                cursor = page["pageInfo"]["endCursor"]
                time.sleep(0.4)           # 页间限速，少惹二级限流
            return False                  # 页数上限

        done = walk("stargazers", "STARRED_AT", "edges{starredAt}",
                    "starredAt", cache["stars"], STAR_PAGE_CAP, None,
                    need=need_days)
        if not cache["stars"]:
            return {}, {}, None
        if not done and len(cache["stars"]) > 1:
            cache["stars"].pop(min(cache["stars"]))  # 最老一天只有半天 → 丢弃
        cache["floor"] = min(cache["stars"])
        walk("forks", "CREATED_AT", "nodes{createdAt}", "createdAt",
             cache["forks"], FORK_PAGE_CAP, cache["floor"])
        t = totals()
        if t:                             # 差分基线，明天起走日常模式
            cache["total_stars"], cache["total_forks"] = t
    cache_file.write_text(json.dumps(cache, sort_keys=True, indent=1))
    return cache["stars"], cache["forks"], cache["floor"]


def gh_repo_rows(cmap, lmap, cov_start):
    """蜡烛 = 仓库每日 commit 数，量柱 = 当日变更行数。"""
    rows = []
    for ds in sorted(cmap):
        if ds < cov_start:
            continue
        c = cmap[ds]
        prev = (date.fromisoformat(ds) - timedelta(days=1)).isoformat()
        o = cmap.get(prev, 0)
        rows.append({"d": ds, "o": o, "h": max(o, c), "l": min(o, c),
                     "c": c, "v": lmap.get(ds, 0)})
    return rows


def contrib_lines_rows(cal, lines, cov_start):
    """蜡烛 = 每日总贡献，量柱 = 当日代码变更行数。"""
    counts = dict(cal)
    rows = []
    for ds, c in cal:
        if c <= 0 or ds < cov_start:
            continue
        prev = counts.get(
            (date.fromisoformat(ds) - timedelta(days=1)).isoformat(), 0)
        rows.append({"d": ds, "o": prev, "h": max(prev, c),
                     "l": min(prev, c), "c": c, "v": lines.get(ds, 0)})
    return rows


def selftest():
    ts = lambda d, h: datetime(2026, 1, d, h).timestamp()
    commits = [(ts(5, 10), 10), (ts(6, 1), 4), (ts(9, 12), 3)]
    rows = daily_ohlc(commits)
    assert [r["d"] for r in rows] == ["2026-01-05", "2026-01-06", "2026-01-09"]
    r1, r2, r3 = rows
    # 开=昨日总量、收=当日总量、高/低=窗口日内极值
    assert (r1["o"], r1["h"], r1["l"], r1["c"]) == (0, 10, 0, 10), r1
    assert (r2["o"], r2["h"], r2["l"], r2["c"]) == (10, 14, 4, 4), r2
    assert (r3["o"], r3["h"], r3["l"], r3["c"]) == (0, 3, 0, 3), r3
    assert [r["v"] for r in rows] == [1, 1, 1]
    ma = [{"c": v} for v in (1, 2, 3, 4, 5, 6)]
    add_ma(ma, 5, "ma5")
    assert [r["ma5"] for r in ma] == [None, None, None, None, 3.0, 4.0]
    gh = daily_rows([("2026-01-05", 0), ("2026-01-06", 8), ("2026-01-07", 3)])
    assert [(r["d"], r["o"], r["h"], r["l"], r["c"]) for r in gh] == [
        ("2026-01-06", 0, 8, 0, 8), ("2026-01-07", 8, 8, 3, 3)]
    cr = commit_rows([("2026-01-05", 5), ("2026-01-06", 9), ("2026-01-07", 2)],
                     {"2026-01-05": 3, "2026-01-06": 7}, "2026-01-05")
    assert [(r["o"], r["h"], r["l"], r["c"], r["v"]) for r in cr] == [
        (0, 3, 0, 3, 5), (3, 7, 3, 7, 9), (7, 7, 0, 0, 2)]
    rr = gh_repo_rows({"2026-01-06": 4, "2026-01-07": 1},
                      {"2026-01-06": 500}, "2026-01-06")
    assert [(r["d"], r["o"], r["h"], r["l"], r["c"], r["v"]) for r in rr] == [
        ("2026-01-06", 0, 4, 0, 4, 500), ("2026-01-07", 4, 4, 1, 1, 0)]
    lr = contrib_lines_rows(
        [("2026-01-05", 5), ("2026-01-06", 9), ("2026-01-07", 2)],
        {"2026-01-05": 120, "2026-01-07": 30}, "2026-01-05")
    assert [(r["o"], r["c"], r["v"]) for r in lr] == [
        (0, 5, 120), (5, 9, 0), (9, 2, 30)]
    # 指标：常数序列 BOLL 三轨重合
    flat = [{"c": 7, "v": 1} for _ in range(25)]
    add_boll(flat)
    assert (flat[-1]["bu"], flat[-1]["bm"], flat[-1]["bl"]) == (7, 7, 7)
    T = dict(title="t", tag="g", meta="m", up="u", down="d",
             vol_word="VOL", vol_unit="x")
    zero_v = [{"d": "2026-03-01", "o": 0, "h": 2, "l": 0, "c": 2, "v": 0}]
    add_indicators(zero_v)
    assert "<svg" in render_svg(zero_v, "light", T)
    fake = [{"d": f"2026-{2 + i // 28:02d}-{i % 28 + 1:02d}", "o": i,
             "h": i + 2, "l": max(0, i - 1), "c": i + 1, "v": i + 1}
            for i in range(1, 60)]
    add_indicators(fake)
    for theme in ("light", "dark"):
        svg = render_svg(fake, theme, T)
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert svg.count("<rect") >= 60 and svg.count("<polyline") >= 6
    print("selftest OK")


# ---------- 静态 SVG（GitHub profile README 不能跑 JS） ----------

# 雪球风格指标线：l1=橙(BOLL上轨/MA5)、l2=蓝(中轨/MA10)、l3=粉(下轨)、l4=紫(MA7)
SVG_PAL = {
    "light": dict(ink="#0b0b0b", ink2="#52514e", muted="#898781",
                  grid="#e1e0d9", axis="#c3c2b7", up="#e34948", down="#008300",
                  l1="#eda100", l2="#2a78d6", l3="#e87ba4", l4="#4a3aa7"),
    "dark":  dict(ink="#ffffff", ink2="#c3c2b7", muted="#898781",
                  grid="#2c2c2a", axis="#383835", up="#e66767", down="#008300",
                  l1="#c98500", l2="#3987e5", l3="#d55181", l4="#9085e9"),
}

SVG_STR = {
    "en": dict(
        tag_github="daily · GitHub contributions",
        tag_repo="daily · file changes",
        tag_commits="daily · commits, volume = contributions",
        tag_lines="daily · contributions, volume = lines changed",
        tag_ghrepo="daily · commits, volume = lines changed",
        tag_ghstars="daily · new stars, volume = new forks",
        up="rise", down="fall", vol_word="VOL",
        vol_github="contrib", vol_repo="commits", vol_commits="contrib",
        vol_lines="lines", vol_ghrepo="lines", vol_ghstars="forks",
        meta_github="last {m} active days · {total} contributions since {first} · updated {today}",
        meta_repo="last {m} active days · {total} file changes since {first} · updated {today}",
        meta_commits="last {m} active days · {wc} commits · {wv} contributions · updated {today}",
        meta_lines="last {m} active days · {wc} contributions · {wv} lines changed · updated {today}",
        meta_ghrepo="last {m} active days · {wc} commits · {wv} lines changed · updated {today}",
        meta_ghstars="last {m} days · +{wc} stars · +{wv} forks · updated {today}"),
    "zh": dict(
        tag_github="日K · GitHub 贡献", tag_repo="日K · 文件修改事项",
        tag_commits="日K · Commit（量柱=总贡献）",
        tag_lines="日K · 贡献（量柱=代码行数）",
        tag_ghrepo="日K · Commit（量柱=代码行数）",
        tag_ghstars="日K · 新增Star（量柱=新增Fork）",
        up="涨", down="跌", vol_word="成交",
        vol_github="贡献", vol_repo="提交", vol_commits="贡献", vol_lines="行",
        vol_ghrepo="行", vol_ghstars="fork",
        meta_github="近 {m} 个活跃日 · 自 {first} 累计贡献 {total} 次 · 更新于 {today}",
        meta_repo="近 {m} 个活跃日 · 自 {first} 累计修改 {total} 项 · 更新于 {today}",
        meta_commits="近 {m} 个活跃日 · commit {wc} 次 · 贡献 {wv} 次 · 更新于 {today}",
        meta_lines="近 {m} 个活跃日 · 贡献 {wc} 次 · 变更 {wv} 行 · 更新于 {today}",
        meta_ghrepo="近 {m} 个活跃日 · commit {wc} 次 · 变更 {wv} 行 · 更新于 {today}",
        meta_ghstars="近 {m} 个交易日 · +{wc} star · +{wv} fork · 更新于 {today}"),
}


def _fmt(v):
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def _tw(s, px):
    """粗略文本宽度估算：CJK 全宽、拉丁 ~0.55em。"""
    return sum(px * (1.0 if ord(c) > 0x2E7F else 0.55) for c in s)


def _nice_ticks(lo, hi, n):
    span = (hi - lo) or 1
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 5, 10) if span / (m * mag) <= n),
                10 * mag)
    out, v = [], math.ceil(lo / step) * step
    while v <= hi + 1e-9:
        out.append(round(v, 2))
        v += step
    return out


def render_svg(rows, theme, t):
    """雪球风格双窗格：价格(BOLL+蜡烛) / 成交量(MA5/MA10)。"""
    p = SVG_PAL[theme]
    W, H = 840, 496
    padL = padR = 10
    plotW = W - padL - padR
    pT, pB = 66, 356          # 价格窗格
    vT, vB = 384, 470         # 成交量窗格
    n = len(rows)
    step = plotW / n
    cw = max(1.5, min(24, step * 0.55))
    x = lambda i: padL + step * (i + 0.5)
    last = rows[-1]

    bands = [r[k] for r in rows for k in ("bu", "bl") if r.get(k) is not None]
    lo = min(min(r["l"] for r in rows), min(bands, default=10 ** 9))
    hi = max(max(r["h"] for r in rows), max(bands, default=0))
    vpad = (hi - lo) * 0.05 or 1
    lo = max(0, lo - vpad)
    hi += vpad
    py = lambda v: pB - (v - lo) / (hi - lo) * (pB - pT)
    maxv = max(r["v"] for r in rows) or 1
    vy = lambda v: vB - v / maxv * (vB - vT - 4)

    e = []
    A = e.append

    def line(x1, y1, x2, y2, col, w=1.0, dash=None):
        A(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
          f'stroke="{col}" stroke-width="{w}"'
          + (f' stroke-dasharray="{dash}"' if dash else "") + '/>')

    def text(tx, ty, s, col, size=11.0, anchor="start", bold=False):
        A(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="{size}" fill="{col}"'
          + (' font-weight="650"' if bold else "")
          + (f' text-anchor="{anchor}"' if anchor != "start" else "")
          + f'>{html.escape(s)}</text>')

    def hdr(y, parts):
        cx = padL + 4
        for s, col in parts:
            text(cx, y, s, col)
            cx += _tw(s, 11) + 10

    def poly(pts_rows, key, yfn, col, w=1.5):
        seg = []
        for i, r in enumerate(pts_rows + [None]):
            if r is None or r.get(key) is None:
                if len(seg) > 1:
                    A(f'<polyline points="{" ".join(seg)}" fill="none" '
                      f'stroke="{col}" stroke-width="{w}" '
                      f'stroke-linejoin="round" stroke-linecap="round"/>')
                seg = []
            else:
                seg.append(f"{x(i):.1f},{yfn(r[key]):.1f}")

    def val(v):
        return "—" if v is None else _fmt(v)

    A(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
      f'width="{W}" height="{H}" '
      f'font-family="-apple-system,&#39;Segoe UI&#39;,system-ui,sans-serif" '
      f'role="img" aria-label="{html.escape(t["title"])} candlestick chart">')
    # 标题行 + 涨跌图例 + 副标题
    text(padL + 4, 20, t["title"], p["ink"], 15, bold=True)
    text(padL + 8 + _tw(t["title"], 15), 20, t["tag"], p["ink2"], 11.5)
    cx = W - 10
    for s, kind in ((t["down"], "down"), (t["up"], "up")):
        cx -= _tw(s, 11)
        text(cx, 20, s, p["ink2"])
        cx -= 15
        if kind == "up":
            A(f'<rect x="{cx:.0f}" y="10" width="9" height="12" '
              f'fill="none" stroke="{p["up"]}" stroke-width="1.5"/>')
        else:
            A(f'<rect x="{cx:.0f}" y="10" width="9" height="12" '
              f'fill="{p["down"]}"/>')
        cx -= 16
    text(padL + 4, 38, t["meta"], p["muted"])

    # ---- 窗格边框 + 月份竖网格线 + 底部时间轴 ----
    for top, bot in ((pT, pB), (vT, vB)):
        A(f'<rect x="{padL}" y="{top}" width="{plotW}" height="{bot - top}" '
          f'fill="none" stroke="{p["grid"]}"/>')
    marks, prev_m = [], None
    for i, r in enumerate(rows):
        m = r["d"][:7]
        if m != prev_m:
            marks.append((i, m))
            prev_m = m
    if len(marks) >= 2:
        lx = -100
        for i, m in marks:
            if i > 0:
                bx = x(i) - step / 2
                for top, bot in ((pT, pB), (vT, vB)):
                    line(bx, top, bx, bot, p["grid"])
            tx = min(max(x(i), padL + 28), padL + plotW - 28)
            if tx - lx >= 64:
                text(tx, H - 6, m, p["muted"], anchor="middle")
                lx = tx
    else:
        for i in range(0, n, max(1, math.ceil(n / 6))):
            text(min(max(x(i), padL + 26), padL + plotW - 26), H - 6,
                 rows[i]["d"][5:], p["muted"], anchor="middle")

    # ---- 价格窗格：网格 + 贴轴刻度 + BOLL + 蜡烛 + 高低点标注 ----
    hdr(58, [("BOLL(20,2)", p["ink2"]),
             (f"UP:{val(last.get('bu'))}", p["l1"]),
             (f"MID:{val(last.get('bm'))}", p["l2"]),
             (f"LOW:{val(last.get('bl'))}", p["l3"]),
             (f"MA7:{val(last.get('ma7'))}", p["l4"])])
    for g in _nice_ticks(lo, hi, 4):
        if not lo <= g <= hi:
            continue
        line(padL, py(g), padL + plotW, py(g), p["grid"])
        text(padL + 4, py(g) - 3, _fmt(g), p["muted"])
    for key, col in (("bu", p["l1"]), ("bm", p["l2"]), ("bl", p["l3"]),
                     ("ma7", p["l4"])):
        poly(rows, key, py, col)
    wick_w = max(1, min(1.5, cw / 6))
    for i, r in enumerate(rows):
        up, down = r["c"] > r["o"], r["c"] < r["o"]
        col = p["up"] if up else p["down"] if down else p["muted"]
        X = x(i)
        top = py(max(r["o"], r["c"]))
        hb = max(abs(py(r["o"]) - py(r["c"])), 1.2)
        if py(r["h"]) < top - 0.5:
            line(X, py(r["h"]), X, top, col, wick_w)
        if py(r["l"]) > top + hb + 0.5:
            line(X, top + hb, X, py(r["l"]), col, wick_w)
        body = (f'x="{X - cw / 2:.1f}" y="{top:.1f}" '
                f'width="{cw:.1f}" height="{hb:.1f}"')
        A(f'<rect {body} fill="none" stroke="{col}" stroke-width="1.3"/>'
          if up else f'<rect {body} fill="{col}"/>')
    hi_i = max(range(n), key=lambda i: rows[i]["h"])
    lo_i = min(range(n), key=lambda i: rows[i]["l"])
    for i, v in ((hi_i, rows[hi_i]["h"]), (lo_i, rows[lo_i]["l"])):
        Y = py(v)
        right = x(i) > W / 2
        ty = Y + 4
        if ty > pB - 4:      # 贴底边时翻到点上方，避免越界撞副图表头
            ty = Y - 6
        elif ty < pT + 12:
            ty = Y + 14
        A(f'<circle cx="{x(i):.1f}" cy="{Y:.1f}" r="2" fill="{p["ink2"]}"/>')
        line(x(i), Y, x(i) + (-8 if right else 8), Y, p["ink2"])
        text(x(i) + (-11 if right else 11), ty, _fmt(v), p["ink2"],
             anchor="end" if right else "start")

    # ---- 成交量窗格 ----
    hdr(376, [(f'{t["vol_word"]} {_fmt(last["v"])} {t["vol_unit"]}', p["ink2"]),
              (f"MA5:{val(last.get('vma5'))}", p["l1"]),
              (f"MA10:{val(last.get('vma10'))}", p["l2"])])
    text(padL + 4, vT + 11, _fmt(maxv), p["muted"], 10)
    for i, r in enumerate(rows):
        up = r["c"] > r["o"]
        col = p["up"] if up else p["down"] if r["c"] < r["o"] else p["muted"]
        vh = vB - vy(r["v"])
        if vh < 0.5:
            continue
        body = (f'x="{x(i) - cw / 2:.1f}" y="{vy(r["v"]):.1f}" '
                f'width="{cw:.1f}" height="{vh:.1f}"')
        A(f'<rect {body} fill="none" stroke="{col}" stroke-width="1.1"/>'
          if up else f'<rect {body} fill="{col}"/>')
    for key, col in (("vma10", p["l2"]), ("vma5", p["l1"])):
        poly(rows, key, vy, col)

    A("</svg>")
    return "\n".join(e)


def add_indicators(rows):
    add_boll(rows)
    add_ma(rows, 7, "ma7")
    add_ma(rows, 5, "vma5", "v")
    add_ma(rows, 10, "vma10", "v")


def write_svgs(rows, outdir, kind, title, lang, days):
    L = SVG_STR[lang]
    add_indicators(rows)
    sub = rows[-days:] if days > 0 else rows
    meta = L["meta_" + kind].format(
        m=len(sub), first=rows[0]["d"], today=date.today().isoformat(),
        total=f"{sum(r['c'] for r in rows):,}",
        wc=f"{sum(r['c'] for r in sub):,}", wv=f"{sum(r['v'] for r in sub):,}")
    t = dict(title=title, tag=L["tag_" + kind], meta=meta,
             up=L["up"], down=L["down"],
             vol_word=L["vol_word"], vol_unit=L["vol_" + kind])
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        (outdir / f"kline-{theme}.svg").write_text(
            render_svg(sub, theme, t), encoding="utf-8")
    print(f"已生成 {outdir}/kline-light.svg + kline-dark.svg")


def main():
    ap = argparse.ArgumentParser(description="git/GitHub 每日活动量 → 股票日K线 HTML")
    ap.add_argument("repo", nargs="?", default=".", help="git 仓库路径（默认当前目录）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--github", metavar="USER",
                   help="用户维度：GitHub 贡献日历（个人主页同款数据），需已登录 gh CLI")
    g.add_argument("--github-repo", metavar="OWNER/NAME",
                   help="仓库维度：该仓库默认分支的每日 commit 数（蜡烛）"
                        "与变更行数（量柱）")
    ap.add_argument("--metric",
                    choices=("contributions", "commits", "lines", "stars"),
                    default="contributions",
                    help="--github 模式：commits=蜡烛为每日commit数、量柱为总贡献；"
                         "lines=蜡烛为每日贡献、量柱为代码变更行数（默认 contributions）。"
                         "--github-repo 模式：stars=蜡烛为每日新增star、量柱为新增fork")
    ap.add_argument("-o", "--out", help="输出 HTML 路径（默认 ./<名字>-kline.html）")
    ap.add_argument("--svg", metavar="DIR",
                    help="改为输出静态 SVG（kline-light.svg + kline-dark.svg），"
                         "用于 GitHub profile README")
    ap.add_argument("--days", type=int, default=60,
                    help="SVG 模式显示最近 N 个活跃日（默认 60，0=全部）")
    ap.add_argument("--lang", choices=("en", "zh"), default="en",
                    help="SVG 文案语言（默认 en）")
    ap.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    ap.add_argument("--selftest", action="store_true", help="运行内置自检")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    if args.metric == "stars" and not args.github_repo:
        sys.exit("--metric stars 仅用于 --github-repo（仓库维度）")

    if args.github_repo:
        full = args.github_repo
        if "/" not in full:
            sys.exit("--github-repo 需要 OWNER/NAME 形式")
        slug = full.replace("/", "-")
        need = args.days + 30 if args.svg and args.days > 0 else 0
        try:
            if args.metric == "stars":
                base = Path(args.svg) if args.svg else Path.cwd()
                base.mkdir(parents=True, exist_ok=True)
                cmap, lmap, cov = github_repo_stars_daily(
                    full, need, base / f"{slug}.stars.json")
            else:
                cmap, lmap, cov = github_repo_daily(full, need)
        except FileNotFoundError:
            sys.exit("需要 GitHub CLI：brew install gh && gh auth login")
        except subprocess.CalledProcessError as e:
            sys.exit(f"gh 失败: {e.stderr.strip()}")
        if not cmap:
            sys.exit("仓库不存在、无权限或没有可统计的数据")
        rows = gh_repo_rows(cmap, lmap, cov)
        if args.metric == "stars":
            kind, title = "ghstars", full
            tag = "日K · 新增Star（量柱=新增Fork）"
            volunit, vollabel = "个", "新增 fork"
            explain = ("收盘 = 当日新增 star · 开盘 = 昨日新增 star · "
                       "量柱 = 当日新增 fork · 零星日休市 · 取消的星不追溯")
            meta = (f"{len(rows)} 个交易日 · +{sum(r['c'] for r in rows):,} star · "
                    f"+{sum(r['v'] for r in rows):,} fork · "
                    f"{rows[0]['d']} ~ {rows[-1]['d']}")
            name = f"{slug}-stars-kline.html"
        else:
            kind, title = "ghrepo", full
            tag = "日K · Commit（量柱=代码行数）"
            volunit, vollabel = "行", "变更行数"
            explain = ("收盘 = 当日 commit 数 · 开盘 = 昨日 commit 数 · "
                       "量柱 = 当日代码变更行数（默认分支全体作者）· 无提交日休市")
            meta = (f"{len(rows)} 个活跃日 · commit {sum(r['c'] for r in rows)} 次 · "
                    f"变更 {sum(r['v'] for r in rows)} 行 · "
                    f"{rows[0]['d']} ~ {rows[-1]['d']}")
            name = f"{slug}-kline.html"
    elif args.github:
        try:
            cal = github_daily(args.github)
            if args.metric in ("commits", "lines"):
                acts = [d for d, c in cal if c > 0]
                if not acts:
                    sys.exit("该账号没有任何贡献记录")
                # SVG 只画最近 days 根蜡烛（+30 根指标预热），深数据只需抓到那里
                need = (acts[-(args.days + 30)]
                        if args.svg and 0 < args.days + 30 < len(acts)
                        else acts[0])
                if args.metric == "commits":
                    cmap, cov = github_commit_daily(
                        args.github, date.fromisoformat(need))
                    rows = commit_rows(cal, cmap, cov)
                else:
                    lmap, cov = github_line_daily(
                        args.github, date.fromisoformat(need))
                    rows = contrib_lines_rows(cal, lmap, cov)
            else:
                rows = daily_rows(cal)
        except FileNotFoundError:
            sys.exit("需要 GitHub CLI：brew install gh && gh auth login")
        except subprocess.CalledProcessError as e:
            sys.exit(f"gh 失败: {e.stderr.strip()}")
        if not rows:
            sys.exit("该账号没有任何贡献记录")
        title = args.github
        if args.metric == "commits":
            kind, tag = "commits", "日K · Commit（量柱=总贡献）"
            volunit, vollabel = "贡献", "贡献次数"
            explain = ("收盘 = 当日 commit 数 · 开盘 = 昨日 commit 数 · "
                       "量柱 = 当日总贡献（commit+PR+issue+review）· 无贡献日休市")
            meta = (f"{len(rows)} 个活跃日 · commit {sum(r['c'] for r in rows)} 次 · "
                    f"贡献 {sum(r['v'] for r in rows)} 次 · "
                    f"{rows[0]['d']} ~ {rows[-1]['d']}")
        elif args.metric == "lines":
            kind, tag = "lines", "日K · 贡献（量柱=代码行数）"
            volunit, vollabel = "行", "变更行数"
            explain = ("收盘 = 当日贡献数 · 开盘 = 昨日贡献数 · "
                       "量柱 = 当日代码变更行数（默认分支 commit 的增+删）· "
                       "无贡献日休市")
            meta = (f"{len(rows)} 个活跃日 · 贡献 {sum(r['c'] for r in rows)} 次 · "
                    f"变更 {sum(r['v'] for r in rows)} 行 · "
                    f"{rows[0]['d']} ~ {rows[-1]['d']}")
        else:
            kind, tag = "github", "日K · GitHub 贡献"
            volunit, vollabel = "贡献", "贡献次数"
            explain = ("收盘 = 当日贡献数 · 开盘 = 昨日贡献数 · "
                       "贡献日历为日粒度，无影线 · 无贡献日休市")
            meta = (f"{len(rows)} 个活跃日 · 累计贡献 {sum(r['c'] for r in rows)} 次 · "
                    f"{rows[0]['d']} ~ {rows[-1]['d']}")
        name = f"{args.github}-github-kline.html"
    else:
        repo = Path(args.repo).resolve()
        try:
            commits = git_commits(repo)
        except subprocess.CalledProcessError as e:
            sys.exit(f"git 失败: {e.stderr.strip() or repo}")
        if not commits:
            sys.exit("仓库没有可统计的提交")
        rows = daily_ohlc(commits)
        kind = "repo"
        title, tag = repo.name, "日K · 文件修改事项"
        volunit, vollabel = "提交", "提交次数"
        explain = ("收盘 = 当日修改事项数 · 开盘 = 昨日总量 · "
                   "高/低 = 24h 滚动窗口日内极值 · 无提交日休市")
        meta = (f"{len(rows)} 个活跃日 · 提交 {sum(v['v'] for v in rows)} 次 · "
                f"修改 {sum(k for _, k in commits)} 项 · "
                f"{rows[0]['d']} ~ {rows[-1]['d']}")
        name = f"{repo.name}-kline.html"

    add_ma(rows, 5, "ma5")
    add_ma(rows, 10, "ma10")

    if args.svg:
        write_svgs(rows, args.svg, kind, title, args.lang, args.days)
        return

    out = Path(args.out) if args.out else Path.cwd() / name
    doc = (TEMPLATE
           .replace("__TITLE__", html.escape(title))
           .replace("__TAG__", tag)
           .replace("__EXPLAIN__", explain)
           .replace("__VOLUNIT__", volunit)
           .replace("__VOLLABEL__", vollabel)
           .replace("__META__", html.escape(meta))
           .replace("__DATA__", json.dumps(rows, ensure_ascii=False,
                                           separators=(",", ":"))))
    out.write_text(doc, encoding="utf-8")
    print(f"已生成 {out}")
    if args.open:
        webbrowser.open(out.as_uri())


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · __TAG__</title>
<style>
  :root { color-scheme: light dark; }
  .viz-root {
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,.10);
    --up: #e34948; --down: #008300; --ma5: #2a78d6; --ma10: #4a3aa7;
  }
  @media (prefers-color-scheme: dark) { .viz-root {
    --surface-1: #1a1a19; --page: #0d0d0d;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,.10);
    --up: #e66767; --down: #008300; --ma5: #3987e5; --ma10: #9085e9;
  } }
  :root.dark .viz-root {
    --surface-1: #1a1a19; --page: #0d0d0d;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,.10);
    --up: #e66767; --down: #008300; --ma5: #3987e5; --ma10: #9085e9;
  }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--page); }
  .viz-root {
    min-height: 100vh; padding: 28px clamp(12px, 4vw, 48px) 48px;
    background: var(--page); color: var(--ink);
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  header h1 { font-size: 20px; font-weight: 650; }
  header h1 .tag { font-size: 12px; font-weight: 500; color: var(--ink-2);
    border: 1px solid var(--border); border-radius: 999px;
    padding: 2px 10px; margin-left: 8px; vertical-align: 3px; }
  .sub { color: var(--ink-2); font-size: 12.5px; margin-top: 4px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
    justify-content: space-between; margin: 18px 0 10px; }
  .ranges { display: flex; gap: 4px; }
  .ranges button { font: 600 12.5px/1 system-ui, sans-serif; color: var(--ink-2);
    background: none; border: 1px solid transparent; border-radius: 7px;
    padding: 7px 12px; cursor: pointer; }
  .ranges button:hover { background: var(--grid); }
  .ranges button[aria-pressed="true"] { color: var(--ink);
    border-color: var(--axis); background: var(--surface-1); }
  .legend { display: flex; gap: 16px; font-size: 12.5px; color: var(--ink-2);
    align-items: center; }
  .legend .it { display: inline-flex; align-items: center; gap: 6px; }
  .sw { width: 10px; height: 12px; border-radius: 2.5px; }
  .sw-up { border: 1.5px solid var(--up); background: var(--surface-1); }
  .sw-down { background: var(--down); }
  .lk { width: 16px; height: 2px; border-radius: 1px; }
  figure { position: relative; background: var(--surface-1);
    border: 1px solid var(--border); border-radius: 12px;
    padding: 10px 6px 6px; }
  svg { display: block; }
  svg:focus-visible { outline: 2px solid var(--ma5); outline-offset: 2px;
    border-radius: 8px; }
  .gridline { stroke: var(--grid); stroke-width: 1; }
  .axisline { stroke: var(--axis); stroke-width: 1; }
  .tick { fill: var(--muted); font-size: 11px;
    font-variant-numeric: tabular-nums; }
  .candle.up .body { fill: var(--surface-1); stroke: var(--up); stroke-width: 1.4; }
  .candle.up .wick { stroke: var(--up); }
  .candle.down .body { fill: var(--down); }
  .candle.down .wick { stroke: var(--down); }
  .candle.flat .body { fill: var(--muted); }
  .candle.flat .wick { stroke: var(--muted); }
  .vol.up { fill: var(--surface-1); stroke: var(--up); stroke-width: 1.2; }
  .vol.down { fill: var(--down); }
  .vol.flat { fill: var(--muted); }
  .ma5 { stroke: var(--ma5); } .ma10 { stroke: var(--ma10); }
  .ma { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .wash { fill: var(--grid); opacity: .45; }
  .xhair { stroke: var(--axis); stroke-width: 1; }
  #tip { position: absolute; top: 0; left: 0; min-width: 168px;
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.12);
    padding: 10px 12px; pointer-events: none; visibility: hidden; z-index: 2; }
  #tip .td { font-weight: 650; font-size: 12.5px; margin-bottom: 6px; }
  #tip .td small { font-weight: 500; color: var(--ink-2); margin-left: 6px; }
  #tip .tr { display: flex; align-items: center; gap: 8px; padding: 1.5px 0;
    font-size: 12.5px; }
  #tip .key { width: 12px; height: 2px; border-radius: 1px; flex: none; }
  #tip .tl { color: var(--ink-2); }
  #tip .tv { margin-left: auto; font-weight: 620; color: var(--ink);
    font-variant-numeric: tabular-nums; }
  details { margin-top: 16px; color: var(--ink-2); }
  summary { cursor: pointer; font-size: 13px; font-weight: 600;
    width: max-content; padding: 4px 2px; }
  .tblwrap { max-height: 340px; overflow: auto; margin-top: 8px;
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; }
  table { border-collapse: collapse; width: 100%; font-size: 12.5px;
    font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 6px 14px; white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  thead th { position: sticky; top: 0; background: var(--surface-1);
    color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--grid); }
  tbody td { color: var(--ink-2); border-bottom: 1px solid var(--grid); }
  tbody td:first-child, tbody td.strong { color: var(--ink); }
  tbody tr:last-child td { border-bottom: none; }
</style>
</head>
<body>
<div class="viz-root">
  <header>
    <h1>__TITLE__<span class="tag">__TAG__</span></h1>
    <p class="sub">__EXPLAIN__ · __META__</p>
  </header>
  <div class="toolbar">
    <div class="ranges" id="ranges" role="group" aria-label="时间范围"></div>
    <div class="legend">
      <span class="it"><span class="sw sw-up"></span>涨（阳线·空心）</span>
      <span class="it"><span class="sw sw-down"></span>跌（阴线·实心）</span>
      <span class="it"><span class="lk" style="background:var(--ma5)"></span>MA5</span>
      <span class="it"><span class="lk" style="background:var(--ma10)"></span>MA10</span>
    </div>
  </div>
  <figure id="fig">
    <svg id="chart" tabindex="0" role="img" aria-label="__TITLE__ K线图，数据见下方数据表"></svg>
    <div id="tip"></div>
  </figure>
  <details>
    <summary>数据表</summary>
    <div class="tblwrap"><table id="tbl">
      <thead><tr><th>日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>涨跌幅</th><th>__VOLUNIT__数</th></tr></thead>
      <tbody></tbody>
    </table></div>
  </details>
</div>
<script>
"use strict";
if (new URLSearchParams(location.search).get("theme") === "dark")
  document.documentElement.classList.add("dark");
const DATA = __DATA__;
const VOLUNIT = "__VOLUNIT__";
const VOLLABEL = "__VOLLABEL__";
const NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("chart");
const fig = document.getElementById("fig");
const tip = document.getElementById("tip");
const fmt = v => v == null ? "—" :
  v.toLocaleString("en-US", { maximumFractionDigits: 1 });
const chg = r => r.o > 0 ? (r.c - r.o) / r.o * 100 : null;
const chgTxt = r => { const p = chg(r);
  return p == null ? (r.c > 0 ? "新增" : "0.0%")
       : (p > 0 ? "▲" : p < 0 ? "▼" : "") + Math.abs(p).toFixed(1) + "%"; };

let view = DATA, active = -1, G = null;

function el(name, attrs, parent) {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function niceTicks(min, max, n) {
  const span = (max - min) || 1, raw = span / n,
    mag = 10 ** Math.floor(Math.log10(raw)),
    step = [1, 2, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag,
    out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step)
    out.push(Math.round(v * 100) / 100);
  return out;
}
function roundTop(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y}` +
    ` L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

function render() {
  svg.textContent = "";
  active = -1; tip.style.visibility = "hidden";
  const W = Math.max(320, fig.clientWidth - 12);
  const H = Math.max(360, Math.min(560, Math.round(W * 0.5)));
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const padL = 10, padR = 56, padT = 12, padB = 26, gap = 20;
  const plotW = W - padL - padR;
  const volH = Math.round((H - padT - padB) * 0.2);
  const priceH = H - padT - padB - volH - gap;
  const n = view.length, step = plotW / n;
  // ponytail: >~1500根时蜡烛重叠成带状(真实行情软件同样如此)；需要更清晰再加周K聚合
  const cw = Math.max(1.5, Math.min(24, step * 0.62));
  const x = i => padL + step * (i + 0.5);

  let lo = Infinity, hi = -Infinity;
  for (const r of view) {
    lo = Math.min(lo, r.l, r.ma5 ?? Infinity, r.ma10 ?? Infinity);
    hi = Math.max(hi, r.h, r.ma5 ?? -Infinity, r.ma10 ?? -Infinity);
  }
  const pad = (hi - lo) * 0.06 || 1;
  lo = Math.max(0, lo - pad); hi += pad;
  const py = v => padT + priceH - (v - lo) / (hi - lo) * priceH;
  const v0 = padT + priceH + gap;
  const maxV = Math.max(...view.map(r => r.v)) || 1;
  const vy = v => v0 + volH - v / maxV * (volH - 4);

  const wash = el("rect", { class: "wash", y: padT, width: Math.max(step, 2),
    height: H - padT - padB, visibility: "hidden" }, svg);

  for (const t of niceTicks(lo, hi, 4)) {
    el("line", { class: "gridline", x1: padL, x2: padL + plotW,
      y1: py(t), y2: py(t) }, svg);
    el("text", { class: "tick", x: padL + plotW + 8, y: py(t) + 4 }, svg)
      .textContent = fmt(t);
  }
  el("line", { class: "axisline", x1: padL, x2: padL + plotW,
    y1: v0 + volH, y2: v0 + volH }, svg);
  el("text", { class: "tick", x: padL + plotW + 8, y: vy(maxV) + 4 }, svg)
    .textContent = fmt(maxV) + " " + VOLUNIT;

  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 88))));
  const long = view.length > 1 &&
    (new Date(view[n - 1].d) - new Date(view[0].d)) > 1.73e10; // >200天
  let prevLabel = "";
  for (let i = 0; i < n; i += every) {
    const label = long ? view[i].d.slice(0, 7) : view[i].d.slice(5);
    if (label === prevLabel) continue;
    prevLabel = label;
    const tx = Math.min(Math.max(x(i), padL + 26), padL + plotW - 26);
    el("text", { class: "tick", x: tx, y: H - 8,
      "text-anchor": "middle" }, svg).textContent = label;
  }

  const wickW = Math.max(1, Math.min(2, cw / 5));
  for (let i = 0; i < n; i++) {
    const r = view[i];
    const dir = r.c > r.o ? "up" : r.c < r.o ? "down" : "flat";
    const g = el("g", { class: "candle " + dir }, svg);
    el("line", { class: "wick", x1: x(i), x2: x(i), y1: py(r.h), y2: py(r.l),
      "stroke-width": wickW }, g);
    const yT = py(Math.max(r.o, r.c));
    const hB = Math.max(Math.abs(py(r.o) - py(r.c)), 1.5);
    el("rect", { class: "body", x: x(i) - cw / 2, y: yT, width: cw,
      height: hB, rx: Math.min(2, cw / 3) }, g);
    el("path", { class: "vol " + dir,
      d: roundTop(x(i) - cw / 2, vy(r.v), cw, v0 + volH - vy(r.v)) }, svg);
  }

  for (const key of ["ma10", "ma5"]) {
    let d = "", pen = false;
    for (let i = 0; i < n; i++) {
      const v = view[i][key];
      if (v == null) { pen = false; continue; }
      d += (pen ? "L" : "M") + x(i).toFixed(1) + "," + py(v).toFixed(1);
      pen = true;
    }
    if (d) el("path", { class: "ma " + key, d }, svg);
  }

  const xhair = el("line", { class: "xhair", y1: padT, y2: v0 + volH,
    visibility: "hidden" }, svg);
  G = { W, H, padL, padT, padB, plotW, step, n, x, v0, volH, wash, xhair };
}

function row(label, value, keyColor) {
  const r = document.createElement("div"); r.className = "tr";
  if (keyColor) { const k = document.createElement("span");
    k.className = "key"; k.style.background = keyColor; r.appendChild(k); }
  const l = document.createElement("span"); l.className = "tl";
  l.textContent = label;
  const v = document.createElement("span"); v.className = "tv";
  v.textContent = value;
  r.append(l, v); return r;
}

function setActive(i) {
  active = i;
  if (i < 0 || !G) {
    tip.style.visibility = "hidden";
    if (G) { G.wash.setAttribute("visibility", "hidden");
      G.xhair.setAttribute("visibility", "hidden"); }
    return;
  }
  const r = view[i], cx = G.x(i);
  G.wash.setAttribute("x", cx - G.step / 2);
  G.wash.setAttribute("visibility", "visible");
  G.xhair.setAttribute("x1", cx); G.xhair.setAttribute("x2", cx);
  G.xhair.setAttribute("visibility", "visible");
  tip.textContent = "";
  const d = document.createElement("div"); d.className = "td";
  d.textContent = r.d;
  const s = document.createElement("small"); s.textContent = chgTxt(r);
  d.appendChild(s); tip.appendChild(d);
  tip.appendChild(row("开盘（昨量）", fmt(r.o)));
  tip.appendChild(row("最高", fmt(r.h)));
  tip.appendChild(row("最低", fmt(r.l)));
  tip.appendChild(row("收盘（今量）", fmt(r.c)));
  tip.appendChild(row(VOLLABEL, fmt(r.v)));
  const css = getComputedStyle(document.querySelector(".viz-root"));
  tip.appendChild(row("MA5", fmt(r.ma5), css.getPropertyValue("--ma5")));
  tip.appendChild(row("MA10", fmt(r.ma10), css.getPropertyValue("--ma10")));
  tip.style.visibility = "visible";
  const tw = tip.offsetWidth;
  const left = cx > G.W / 2 ? cx - tw - 14 : cx + 14;
  tip.style.transform = `translate(${Math.max(4, left)}px, ${G.padT + 6}px)`;
}

svg.addEventListener("pointermove", e => {
  if (!G) return;
  const b = svg.getBoundingClientRect();
  const i = Math.floor((e.clientX - b.left - G.padL) / G.step);
  setActive(Math.max(0, Math.min(G.n - 1, i)));
});
svg.addEventListener("pointerleave", () => setActive(-1));
svg.addEventListener("keydown", e => {
  if (!G) return;
  const map = { ArrowLeft: Math.max(0, (active < 0 ? G.n : active) - 1),
    ArrowRight: Math.min(G.n - 1, active + 1), Home: 0, End: G.n - 1 };
  if (e.key in map) { setActive(map[e.key]); e.preventDefault(); }
  else if (e.key === "Escape") setActive(-1);
});
svg.addEventListener("focus", () => { if (active < 0) setActive(view.length - 1); });

const RANGES = [["近1月", 1], ["近3月", 3], ["近1年", 12], ["全部", 0]];
const spanDays = (new Date(DATA[DATA.length - 1].d) - new Date(DATA[0].d)) / 864e5;
let curRange = spanDays > 370 ? 12 : 0;
const rangeParam = new URLSearchParams(location.search).get("range");
if (rangeParam !== null && RANGES.some(r => r[1] === +rangeParam))
  curRange = +rangeParam;
function applyRange(months) {
  curRange = months;
  if (!months) view = DATA;
  else {
    const cut = new Date(DATA[DATA.length - 1].d);
    cut.setMonth(cut.getMonth() - months);
    view = DATA.filter(r => new Date(r.d) >= cut);
    if (view.length < 2) view = DATA.slice(-2);
  }
  for (const b of document.querySelectorAll("#ranges button"))
    b.setAttribute("aria-pressed", String(+b.dataset.m === months));
  render();
}
for (const [label, m] of RANGES) {
  const b = document.createElement("button");
  b.type = "button"; b.dataset.m = m; b.textContent = label;
  b.setAttribute("aria-pressed", "false");
  b.addEventListener("click", () => applyRange(m));
  document.getElementById("ranges").appendChild(b);
}

const tbody = document.querySelector("#tbl tbody");
for (let i = DATA.length - 1; i >= 0; i--) {
  const r = DATA[i], tr = document.createElement("tr");
  const cells = [r.d, fmt(r.o), fmt(r.h), fmt(r.l), fmt(r.c), chgTxt(r), fmt(r.v)];
  cells.forEach((c, j) => { const td = document.createElement("td");
    td.textContent = c; if (j === 4) td.className = "strong";
    tr.appendChild(td); });
  tbody.appendChild(tr);
}

let raf = 0;
new ResizeObserver(() => {
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(() => applyRange(curRange));
}).observe(fig);
applyRange(curRange);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
