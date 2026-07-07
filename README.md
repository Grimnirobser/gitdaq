# gitdaq 📈

**Your GitHub contributions, traded like a stock.**

gitdaq renders your GitHub activity as a **candlestick (K-line) chart** — right
on your profile README. Red means you "rallied" (more than yesterday), green
means you "sold off". Chinese A-share color convention, and a Xueqiu-style
terminal layout: **BOLL(20,2) bands** over the candles and a **volume pane
with MA5/MA10** — plus period high/low callouts, the whole trading-app look.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Grimnirobser/Grimnirobser/main/kline/kline-dark.svg">
  <img alt="Demo: GitHub contributions as a candlestick chart" src="https://raw.githubusercontent.com/Grimnirobser/Grimnirobser/main/kline/kline-light.svg">
</picture>

*Live demo above — regenerated daily from [@Grimnirobser](https://github.com/Grimnirobser)'s
contribution calendar by this very action.*

## Put it on your profile

1. In your profile repository (`<username>/<username>`), create
   `.github/workflows/kline.yml`:

   ```yaml
   name: kline
   on:
     schedule:
       - cron: "17 0 * * *"   # daily; pick any time
     workflow_dispatch:
   permissions:
     contents: write
   jobs:
     kline:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: Grimnirobser/gitdaq@v1
         - name: Commit chart
           run: |
             git config user.name "github-actions[bot]"
             git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
             git add kline
             git diff --cached --quiet || git commit -m "chore: refresh kline chart"
             git push
   ```

2. Add the chart to your `README.md`:

   ```html
   <picture>
     <source media="(prefers-color-scheme: dark)" srcset="kline/kline-dark.svg">
     <img alt="My GitHub contributions as a candlestick chart" src="kline/kline-light.svg">
   </picture>
   ```

3. Run the workflow once from the Actions tab (or wait for the cron) — done.

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `user` | repo owner | GitHub username to chart |
| `github_token` | `github.token` | Default token sees **public** contributions; pass a PAT with `read:user` to include private counts |
| `metric` | `contributions` | `contributions`: candles = daily total contributions. `commits`: candles = daily **commits**, volume = contributions. `lines`: candles = contributions, volume = **lines of code changed** (adds + deletes on default branches) |
| `days` | `60` | Recent active days to plot (`0` = entire history) |
| `lang` | `en` | Chart labels: `en` or `zh` |
| `output_dir` | `kline` | Where `kline-light.svg` / `kline-dark.svg` are written |

## How the OHLC is defined (honest data, no invented wicks)

The "price" is your **daily contribution count** (same data as the green
heatmap: commits + PRs + issues + reviews, via the GraphQL contribution
calendar).

| Element | Meaning |
|---|---|
| Close | today's contribution count |
| Open | yesterday's contribution count |
| Rise / fall | did you do more or less than yesterday |
| Volume | contribution count, as bars |
| No-activity days | skipped, like non-trading days; comebacks gap like a resumed stock |

The calendar is day-granular, so profile candles have no wicks — we'd rather
draw no wick than a fabricated one. Up candles are hollow, down candles are
filled, so direction never relies on color alone (red-green colorblind safe;
palette CVD-validated).

With `metric: commits` the candles switch to **daily commits** (GitHub
attribution rules: default-branch commits, linked author email) while the
volume bars keep showing total contributions — commit price action on top,
overall market activity below.

With `metric: lines` the candles stay on contributions and the volume bars
show **lines of code changed** per day (additions + deletions of your
authored commits on each repo's default branch) — price action on top, how
much code actually moved below. Same attribution rules as commits.

## Local analysis mode (interactive HTML)

The same script doubles as a local tool with crosshair tooltips, range
switching and a data table. Here the price is the **rolling 24-hour count of
file modifications** (one file changed in one commit = 1 event), which has
real intraday extremes — so these candles *do* have honest wicks:
open = yesterday's total (the window value at midnight), close = today's
total, high/low = the window's intraday extremes.

```bash
python3 kline.py /path/to/repo --open          # any local git repo → HTML
python3 kline.py --github <user> --open        # your whole GitHub account → HTML
python3 kline.py --github <user> --svg out/    # the profile SVGs, locally
python3 kline.py --selftest                    # built-in checks
```

Zero dependencies: Python 3 stdlib only; SVG/HTML are self-contained.
(`--github` needs an authenticated [GitHub CLI](https://cli.github.com/) when
run locally; inside Actions the runner's `gh` + workflow token are used.)

---

## 中文速览

把 GitHub 贡献画成**股票日 K 线**挂在个人主页：红涨绿跌（A 股配色）、阳线空心阴线实心、
雪球风格布局（BOLL 布林带 + 量柱带均量线）、最高/最低点标注。
收盘 = 今日贡献数，开盘 = 昨日贡献数；无贡献日如休市跳过。
贡献日历只有日粒度，因此主页蜡烛无影线——宁缺毋滥，不编造数据。

接入方法：在你的 profile 仓库（`用户名/用户名`）里按上面第 1、2 步添加 workflow 和
`<picture>` 标签即可，`lang: zh` 可切中文图表文案。本地模式（交互 HTML、仓库文件修改
K线、带真实影线）见上节命令。

## License

MIT
