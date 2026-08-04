# Sentinel-AI Documentation

本目录定义 Sentinel-AI V1 的工程契约。实现应遵循这些接口、数据模型和安全要求；任何破坏性变更必须同时更新相关文档与测试。

| 文档 | 内容 |
| --- | --- |
| [Database ER Diagram](database-er.md) | PostgreSQL/pgvector 数据模型、约束与索引 |
| [API Specification](api.md) | REST API、请求响应、错误与分页规范 |
| [UI Design](ui-design.md) | 页面、状态、交互、响应式与可访问性规范 |
| [Prompt Design](prompt-design.md) | 结构化 LLM Prompt、引用校验与安全边界 |
| [Hybrid Retrieval](retrieval.md) | FTS、pgvector、RRF 与 Embedding Provider 边界 |
| [Environment](environment.md) | 本地、Docker、测试与生产环境变量规范 |
| [CI](ci.md) | GitHub Actions 质量门禁与发布前检查 |