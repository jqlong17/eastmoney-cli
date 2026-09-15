---
name: ashare-swing
description: >-
  Analyzes China A-share stocks for medium/short-term (中短线) swings using
  emquote chart CLIs and condition-order levels. Draws daily/K-line/channel/
  price-volume/intraday charts and produces structured research notes with
  watch levels. Use when the user mentions a stock code or name, 炒股, 看盘,
  中短线, 条件单, 买卖点, or asks to chart/analyze an A-share.
---

# A 股中短线看盘（ashare-swing）

用本仓库 `emquote` / `emplot-*` **只读画图 + 条件单参考价**，做**中短线研究备注**。  
不下单；输出必须带风险声明。

## 何时启用

- 用户提到股票代码（`603606` / `603606.SH` / `002223.SZ`）或明确个股名
- 用户说：炒股、看盘、中短线、波段、条件单、支撑压力、买卖点、帮我看看这只票

## 固定工作流

```
进度:
- [ ] 1. 解析标的代码
- [ ] 2. 拉取报价
- [ ] 3. 出图（日线 / 5mK线 / 价量通道 / 分时）
- [ ] 4. 取条件单参考价
- [ ] 5. 按模板写中短线备注
```

### 1) 解析代码

- 6 位数字：`6/9/5` 开头 → `.SH`，否则常见深市 → `.SZ`
- 后缀统一大写（`603606.sh` → `603606.SH`）
- 不确定先 `emquote quote <猜测>`，失败再问用户

### 2–4) 一键出图 + 数据（优先）

仓库根目录、已 `pip install -e '.[plot]'`：

```bash
python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH -o /tmp/swing-603606
```

K 线节点挂掉时：

```bash
python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH --demo -o /tmp/swing-603606
```

脚本会：

1. `emquote quote --json`（在线报价；demo 模式仍尽量拉报价）
2. 四张图：`daily` / `kline` / `pv` / `intraday`
3. `emquote levels --json`（通道 `vwreg,donchian`）
4. 打印 JSON，并写入 `{outdir}/{code}-brief.json`

live 画图失败时，脚本会自动回退到：

- `examples/sample-603606-5m.json`
- `examples/sample-603606-1d.json`

并在 `errors` 里标注 fallback。此时要明确告诉用户：**图可能是离线样例，报价若成功则以报价为准**。

手工等价：

```bash
emquote quote 603606.SH --json
emplot-daily 603606.SH -o /tmp/daily.png --json
emplot-kline 603606.SH -o /tmp/kline.png --json
emplot-pv 603606.SH -o /tmp/pv.png --json
emplot-intraday 603606.SH -o /tmp/intraday.png --json
emquote levels 603606.SH -i 5m --days 10 --channel vwreg,donchian --json
```

**必须打开 PNG 读图**，不要只报路径。

### 5) 中短线备注模板（必须按此输出）

```markdown
## 中短线看盘 · {名称} {代码}

**时效**：基于 {报价时间} 公开行情快照；研究备注，非投资建议。

### 结论（先看这三句）
- 位置：{偏强/震荡/偏弱}（依据日线均线与通道）
- 计划：{观望 / 等回踩 / 等突破} —— 一句话
- 关键风险：{一句话}

### 图表
- 日线：`{daily.png}`
- 5 分钟 K 线：`{kline.png}`
- 价量通道 + 条件单：`{pv.png}`
- 分时：`{intraday.png}`

### 关键价位（条件单可抄，人工下单）
| 用途 | 参考价 | 用法 |
|---|---|---|
| 回踩关注/买入区 | {buy} | 东财条件单「价格≤」类 |
| 反弹关注/卖出区 | {sell} | 「价格≥」类 |
| 防守止损 | {stop} | 跌破则计划失效 |

### 中短线推理（简短）
1. **日线**：MA5/10/20/60 排列与收盘相对位置 → 大方向
2. **5m 通道/价量**：是否贴上轨/下轨、有无触轨放量 → 进出场节奏
3. **分时**：相对昨收与 VWAP → 当日强弱

### 操作框架（中短线，不是荐股）
- **更积极**：日线未破关键均线 + 5m 回踩通道下轨/中轴且量能不恶化
- **更保守**：日线在均线下方或通道下破 → 先观望，等站回再看
- **失效条件**：收盘跌破止损参考价 / 分时放量跌破昨收且不收回

### 声明
只读公开行情研究；不下单、不保证收益；请自行风控。
```

## 分析纪律（中短线）

- **周期默认**：日线定方向，5 分钟定买卖带，分时定当日节奏
- **先位置后预测**：先说「在哪」，再说「等什么」
- **价位落到数字**：优先用 `levels` / 图上条件单参考价
- **禁止**：保证赚、喊单「立刻满仓」、伪装实时柜台成交
- **网络失败**：报价可用但 K 线失败 → 说明缺图原因，可用 `--demo`/自动 fallback，并标注样例图

## 额外资料

- 输出样例：[examples.md](examples.md)
- CLI 说明：仓库根目录 `README.md`
