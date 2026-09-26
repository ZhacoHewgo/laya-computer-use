# 有界等待、配置解析与自动检查 — 2026-09-27

## 本轮修改

1. 已到达目标详情页、但正文未出现时，只等待一轮（最多 12 次），之后带明确原因停止，不再请求修复计划后重新等待。读取仍可等待异步正文，没有重试浏览器写操作。
2. Inspector 使用 python-dotenv 解析当前目录的 `.env`：支持引号、空格、注释、export、UTF-8 BOM；外部环境变量优先，禁止变量插值。浏览器模块延迟到环境加载之后导入。
3. 新增 `.github/workflows/checks.yml`：main 推送、PR 或手动触发；只读仓库权限，固定第三方 Action 提交版本，15 分钟上限。不读取仓库密钥，不运行真实网站任务。
4. pytest 默认禁止建立外部 socket 连接，避免离线测试误调用付费模型或下载权重。
5. 更新示例配置的调用次数说明、localhost 代理与本地模型的区别，以及 README 中过时的“最新测试”和文档归属说明。

## 实测

| 场景 | 结果 |
| --- | --- |
| 只有导航、无正文 | 38 次等待降为 12 次，然后 BLOCKED；0 次规划 API（预设计划） |
| 正文延迟 600ms 加载 | 等待 3 次后成功（预设计划） |
| 付费墙提示 | 0 个动作，直接停止（预设计划） |
| 讨论付费模式的正常正文 | 正常完成（预设计划） |
| Wikipedia | 用户 API + Laya，通过；1 次规划、2 个动作 |
| Google Flights | 用户 API + Laya，9 项检查通过；1 次规划、12 个动作；未预订 |
| travel / research / zh | 预设计划 + Laya，全部通过 |

离线测试 **208 passed**。Ruff、JS 语法、锁文件一致性、构建及差异格式检查通过。

另建临时干净 Python 环境，未安装 laya-mlx 或 mlx，安装 CI 所需依赖后重新运行：208 项测试、lint、源码包与 wheel 构建，以及 wheel 内 JS/HTML 资源检查通过。此验证在本机 macOS 上完成；**不是 GitHub Linux runner 的执行记录**。工作流 YAML 已解析检查，真正的 GitHub 运行需要推送之后验证。

本轮真实规划请求 2 次，合计 4,248 tokens，实际费用未知。所有离线/预设计划测试未调用规划 API。

## 证据

[汇总记录](onboarding-20260927-measurement.json)。原始轨迹位于 Git 忽略的：

- `artifacts/onboarding-content-20260927`
- `artifacts/onboarding-delayed-body-20260927`
- `artifacts/onboarding-live-20260927`
- `artifacts/onboarding-fixtures-20260927`

## 边界

12 次等待是有限加载预算，非常慢的网站仍可能被保守停止。CI 不测试真实 MLX 推理、Chrome 控制或网站准确率。依赖安装与构建阶段需要网络；pytest 执行阶段禁止外部连接。CI 依赖遵循项目版本范围，未完全复制本地 MLX 锁定环境。

本轮及上一轮未提交的修改仍在本地，未推送；8770 常驻服务未重启。
