# 模型成本对比报告

> 用途：记录同一任务在不同 LLM 上的 token 消耗与成本，评估性价比。
> 数据来源：`cost_tracker.report()` 或 `cost_tracker.save()` 导出的统计。

- 对比日期：YYYY-MM-DD
- 数据版本：knowledge/raw/raw_YYYYMMDD_HHMMSS.json

## 1. 测试条件

| 项目 | 说明 |
|------|------|
| 任务类型 | 例：40 篇文章摘要分析（Step 2） |
| 输入数据量 | 例：40 条原始数据，平均每条 120 字 |
| 提示词模板 | 例：ANALYZE_PROMPT_TEMPLATE |
| 参数 | 例：temperature=0.3, max_tokens=500 |
| 其他说明 | 例：含重试 1 次 |

## 2. DeepSeek Chat

| 指标 | 数值 |
|------|------|
| 模型 | deepseek-chat |
| 调用次数 | - |
| Prompt tokens | - |
| Completion tokens | - |
| 总 tokens | - |
| 估算成本（元） | - |
| 单价（元/百万 tokens） | 输入 1 / 输出 2 |

## 3. Qwen Plus（如已配置）

| 指标 | 数值 |
|------|------|
| 模型 | qwen-plus |
| 调用次数 | - |
| Prompt tokens | - |
| Completion tokens | - |
| 总 tokens | - |
| 估算成本（元） | - |
| 单价（元/百万 tokens） | 输入 4 / 输出 12 |

> 未配置 QWEN_API_KEY 时，本节可整段删除。

## 4. 结论

| 对比项 | DeepSeek Chat | Qwen Plus |
|--------|--------------|-----------|
| 总成本（元） | - | - |
| 单条平均成本（元） | - | - |
| 输出质量评分（1-10） | - | - |

**性价比结论：**

- [ ] 结论与理由（例：DeepSeek 成本低 X 倍，摘要质量差异可忽略，建议作为默认 provider）
- [ ] 后续动作（例：更新 LLM_PROVIDER 环境变量 / 调整 PRICING）
