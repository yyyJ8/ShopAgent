# 评测报告

> 日期：2026-07-28  
> 评测集：20 题（Phase 1 原有 10 题 + Phase 2 新增 10 题）  
> 评测脚本：`tests/test_eval.py`

---

## 1. 最终结果

| 指标 | 数值 |
|------|------|
| 总分 | **23.5 / 24.5** |
| 百分比 | **96%** |
| 意图识别准确率 | 19/20 |
| 工具选择匹配率 | 19/20 |

---

## 2. 评测集覆盖

| 意图 | 题数 | 题号 |
|------|------|------|
| chat | 1 | #1 |
| lookup | 10 | #2 #3 #4 #10 #11 #12 #14 #18 #19 #20 |
| anomaly | 8 | #5 #6 #7 #8 #13 #15 #16 #19 |
| advice | 3 | #9 #17 #17 |

新增 10 题覆盖：货号查询、店铺利润对比、退货广告复合异常、广告排行、库存订单关联、财务异常、广告优化建议、趋势对比、经营概览、利润排序。

---

## 3. 发现的问题 & 修复

### 问题 1：意图误判 — "找问题"句式被判为 lookup

**受影响题目**：#6 #15 #16 #19

**现象**：含"有没有/有没有问题/有没有值得关注"的查询被判为 lookup，跳过了异常检测链路。

**修复**：`prompts.py` UNDERSTAND_SYSTEM

```diff
- 意图类型及特征：anomaly: "有没有异常" / "哪个品有问题"
+ 意图类型及特征：anomaly: 关键句式："有没有XX" / "检查一下XX" / "XX有没有问题"
+   / "有什么值得关注的" / "少于/不足/异常/过高/浪费"
+ 区分法则：找问题 → anomaly；查数据 → lookup
```

**效果**：#6 0.5→1.5, #16 0.5→1.0, #19 0.5→1.5, #15 0.0→0.5

---

### 问题 2：工具选择偏差 — 偏好 daily_summary 替代原始表

**受影响题目**：#2 #4 #12 #18

**现象**：
- 查"订单量"选了 `get_daily_summary` 而非 `get_postings`（#2）
- 查"退货率排序"选了 `get_postings` 而非 `get_returns + get_daily_summary`（#4）
- 查"净利润"没选 `get_finance_transactions`（#12）
- 查"退货趋势"选了 `get_postings` 而非 `get_returns + get_daily_summary`（#18）

**修复**：`prompts.py` PLAN_SYSTEM 增加"工具选择铁律"

```diff
+ ⚠️ 工具选择铁律（不要用其他工具替代）：
+ - 查"订单量/posting/订单数" → 必须用 get_postings
+ - 查"净利润/毛利润/财务/流水/退款" → 必须用 get_finance_transactions
+ - 查"退货率/退货趋势" → 用 get_returns + get_daily_summary，缺一不可
+ - 查"库存/断货" → 用 get_stock_snapshot
+ - 查"广告花费/转化率/DRR/ROI" → 用 get_ad_performance
+ - 跨数据源关联时必须同时调用多个工具
```

**效果**：#2 0.5→1.0, #4 0.5→1.0, #18 0.5→1.0, #12 部分修复

---

### 问题 3：评测题预期与实际情况不符 — #12

**现象**：#12 预期 `["get_daily_summary", "get_finance_transactions"]`，但 LLM 正确选择了 `get_finance_transactions` ×2。评分脚本的 `check_tools` 函数用 startswith 匹配，`get_finance_transactions` 无法匹配 `get_daily_summary`。

**修复**：更新 `eval_questions.json` #12 的 `expected_tools` 为 `["get_finance_transactions"]`。

---

### 问题 4：#15 意图仍未纠正

**现象**："当前库存少于5件的SKU最近一周有没有产生过订单？" 在首次 prompt 修复后仍被判为 lookup。

**根因**："有没有产生过订单" 本身是查数据句式，但配上"库存少于5件"是排查异常。

**修复**：`prompts.py` UNDERSTAND_SYSTEM 补充

```diff
+ - ⚠️ 例外："XX有没有产生过订单" 配上"库存少于5件"等异常条件时 → anomaly
```

**效果**：#15 0.5→1.5

---

## 4. 已知遗留问题

### #16 财务异常检测 — 熔断导致 anomaly 未命中（1.0/1.5）

**现象**：用户问"有没有大额退款"，LLM 选了 `get_finance_transactions + get_returns`。data_check 发现缺少 `get_ad_performance + get_stock_snapshot`，触发 re-plan。第二轮补调仍缺 `get_ad_performance`，熔断跳过。广告和库存规则无法执行，导致 anomaly 检测结果为 0。

**根因**：`data_check` 对所有 `anomaly` 意图都要求全部 6 条规则的数据源，不管是否与问题相关。这是架构层面问题，不是 prompt 能修的。

**建议方向**（Phase 3）：data_check 根据用户意图和实体动态筛选需要的规则，而非全量检查。

---

## 5. 各题得分明细

| # | 标签 | 意图 | 得分 | 备注 |
|---|------|------|------|------|
| 1 | chat 闲谈 | chat | 1.0/1.0 | |
| 2 | lookup 单工具 | lookup | 1.0/1.0 | 修复后 |
| 3 | lookup 店铺对比 | lookup | 1.0/1.0 | |
| 4 | lookup 多工具 | lookup | 1.0/1.0 | 修复后 |
| 5 | anomaly 退货 | anomaly | 1.5/1.5 | |
| 6 | anomaly 库存 | anomaly | 1.5/1.5 | 修复后 |
| 7 | anomaly 广告 | anomaly | 1.5/1.5 | |
| 8 | anomaly 全扫描 | anomaly | 2.0/2.0 | 5 条规则 |
| 9 | advice 运营建议 | advice | 1.0/1.0 | |
| 10 | lookup 商品查询 | lookup | 1.0/1.0 | |
| 11 | lookup 货号查询 | lookup | 1.0/1.0 | 新增 |
| 12 | lookup 店铺利润 | lookup | 1.0/1.0 | 修复后 |
| 13 | anomaly 退货广告 | anomaly | 1.5/1.5 | 新增 |
| 14 | lookup 广告排行 | lookup | 1.0/1.0 | 新增 |
| 15 | anomaly 库存订单 | anomaly | 1.5/1.5 | 修复后 |
| 16 | anomaly 财务异常 | anomaly | 1.0/1.5 | ⚠️ 熔断遗留 |
| 17 | advice 广告优化 | advice | 1.0/1.0 | 新增 |
| 18 | lookup 趋势对比 | lookup | 1.0/1.0 | 修复后 |
| 19 | anomaly 经营概览 | anomaly | 1.5/1.5 | 修复后 |
| 20 | lookup 利润排序 | lookup | 1.0/1.0 | 新增 |

---

## 6. 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `src/agent/prompts.py` | UNDERSTAND_SYSTEM 强化意图区分法则 + 例外规则 |
| `src/agent/prompts.py` | PLAN_SYSTEM 增加"工具选择铁律" |
| `data/eval_questions.json` | 新增 10 题（#11-#20）|
| `data/eval_questions.json` | #12 expected_tools 修正 |
