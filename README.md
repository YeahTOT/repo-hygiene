# Repo Hygiene — 算法仓库治理 Skill

面向 **Python / PyTorch / YOLO / 计算机视觉** 等算法研发项目的仓库治理技能，用于识别冗余代码、重复实现、实验脚本堆积、目录不规范和误提交的训练产物。

## 核心能力

| 模式 | 用途 | 默认是否修改文件 |
| --- | --- | --- |
| **Audit（体检）** | 只读扫描、风险分级、提出清理建议 | 否 |
| **Refactor（整理）** | 在明确授权后逐步重构并验证兼容性 | 需要授权 |
| **Guard（门禁）** | 检查变更范围与已有质量工具结果 | 否 |

特别关注机器学习项目常见的 **Hydra/YAML 配置引用、模型注册器、PyTorch checkpoint、ONNX/TensorRT/Ascend OM 导出接口、对照实验可复现性**。发现疑似废弃代码时，**不会仅凭静态分析结果自动删除**。

## 快速运行检查脚本

仅需 Python 标准库：

```bash
python3 scripts/repo_audit.py --root /path/to/your-project

# 输出 JSON 报告
python3 scripts/repo_audit.py --root . --json

# 只检查当前未提交的变更
python3 scripts/repo_audit.py --root . --changed-from HEAD

# 可选的顶层目录白名单
python3 scripts/repo_audit.py --root . --allowed-top src,configs,scripts,tests,experiments,docs

# 在 CI 中对高优先级问题返回失败状态
python3 scripts/repo_audit.py --root . --fail-on high
```

脚本能识别文件内容完全相同、可疑的 `_final`/`_v2` 命名、生成产物、异常顶层目录和过长 Python 文件。它**不是**函数可达性分析或语义重复检测器；建议结合 Ruff、Vulture、Import Linter、pytest 等工具人工核实。

## 作为 Agent Skill 使用

将本仓库中的 `SKILL.md` 与 `scripts/`、`references/`、`agents/` 等文件作为一个完整技能目录，按所使用的 Coding Agent 的 Skill 加载方式安装或引用。

示例提示词：

> 使用 `$repo-hygiene` 的 Audit 模式，检查当前算法仓库的目录结构、重复实现、无用代码及实验文件。只生成中文体检报告，不修改或删除文件。

完整行为规范见 [SKILL.md](SKILL.md)，详细规则见 [references/algorithm-repo.md](references/algorithm-repo.md)。

## 测试

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

## 安全原则

- 只读体检与质量门禁默认不修改项目。
- 删除或移动模型实现前，必须检查配置、注册器、脚本入口、部署接口以及实验引用。
- 对于不确定的删除、公开 API 变更或大型权重与数据移动，先征求用户确认。
- 未添加开源许可证。公开可见并不自动授予代码复用、修改或分发许可。
