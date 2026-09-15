---
name: ashare-swing
description: >-
  Builds China A-share medium-term swing (几天到几周) condition-order plans for
  busy office workers using emquote charts and standardized buy/take-profit/
  stop levels with validity and invalidation rules. Use when the user mentions
  a stock code/name, 上班族, 波段, 中短线, 条件单, 几天到几周, or asks to set
  alerts instead of watching the market. Not for intraday scalping or limit-up chasing.
---

# A 股上班族波段条件单（ashare-swing）

服务**几天到几周**的波段交易：用户没法盯盘，用东方财富**条件单**提前挂好买卖止损。  
本 skill 只读公开行情、出图、给出可抄录的条件单方案；**不下单、不做超短/打板**。

## 产品边界

- ✅ 做：日线定方向 + 5 分钟价量通道定触发带 + 标准化条件单方案
- ✅ 做：买入区 / 止盈 / 止损 / 建议有效期（约 3～10 个交易日）/ 失效条件
- ⚠️ 分时图仅辅证，不作为设单主依据（默认可不画）
- ❌ 不做：日内超短、打板、竞价、自动下单、盯盘推送

## 何时启用

- 用户提到股票代码/名称，或说：波段、中短线、条件单、上班没空盯盘、挂单、几天到几周

## 固定工作流

```
进度:
- [ ] 1. 解析标的代码
- [ ] 2. 拉取报价（可走缓存）
- [ ] 3. 出主图：日线 / 5m K线 / 价量通道（分时可选）
- [ ] 4. 生成标准化条件单方案 emquote plan
- [ ] 5. 按模板写波段备注（含有效期与失效条件）
```

### 1) 解析代码

- 6 位：`6/9/5` → `.SH`，否则常见 `.SZ`；后缀大写
- 不确定先 `emquote quote <猜测>`

### 2–4) 一键简报（优先）

```bash
python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH -o /tmp/swing-603606
# K 线不稳时：
python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH --demo -o /tmp/swing-603606
# 需要分时辅图时再加：
python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH --with-intraday -o /tmp/swing-603606
```

脚本产出：

1. 报价 JSON  
2. 主图 `daily` / `kline` / `pv`（可选 `intraday`）  
3. `levels` + **`plan`（标准化条件单方案）**  
4. `{outdir}/{code}-brief.json`

手工等价：

```bash
emquote quote 603606.SH --json
emplot-daily 603606.SH -o /tmp/daily.png --json
emplot-kline 603606.SH -o /tmp/kline.png --json
emplot-pv 603606.SH -o /tmp/pv.png --json
emquote plan 603606.SH -i 5m --days 10 --channel vwreg,donchian --json
```

K 线有本地缓存（`~/.cache/emquote/kline/`）；失败会回退缓存/离线样例。强制重拉用 `--refresh`。

**必须读 PNG + plan 数字**，不要只报路径。

### 5) 输出模板（必须）

```markdown
## 波段条件单 · {名称} {代码}

**定位**：几天到几周 · 条件单代替盯盘 · 研究备注非投资建议  
**时效**：基于 {报价时间} 公开行情

### 结论（三句）
- 方向：{偏强/震荡/偏弱}（看日线）
- 计划：{观望 / 等回踩买入区 / 等突破} —— 一句话
- 关键风险：{一句话}

### 条件单方案（可抄东财）
| 项目 | 价格/区间 | 东财用法 | 建议有效期 |
|---|---|---|---|
| 买入区 | {buy_low}～{buy_high}（触发 {buy}） | 价格≤ / 区间触达 | {N} 个交易日 |
| 止盈 | {sell} | 价格≥ | 同左 |
| 止损 | {stop} | 价格≤ | 同左 |

有效期建议：**{N} 个交易日**（常见 3～10 日，到期未触发需复盘重挂）。

### 失效条件
- {invalidation_1}
- {invalidation_2}
- {invalidation_3}

### 图表（主）
- 日线：`{daily.png}` —— 定方向
- 5m K 线：`{kline.png}` —— 看结构
- 价量通道：`{pv.png}` —— 定触发带

### 辅图（可选）
- 分时：仅辅助感受当日强弱，**不作为设单主依据**

### 声明
只读研究；人工录入条件单；不下单、不保证收益。
```

## 纪律

- 主叙事永远是「波段 + 条件单」，不要滑向盯盘/超短
- 价位必须来自 `emquote plan` / 图上标注
- 禁止保证收益、喊单满仓、伪装成交能力
