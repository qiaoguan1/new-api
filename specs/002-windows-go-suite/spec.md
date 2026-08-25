# Feature Specification: Windows 全量 Go 测试稳定性

**Feature Branch**: `codex/144-windows-go-suite`
**Created**: 2026-08-25
**Status**: In Progress
**Input**: 支付生产基线在 Windows Go 1.26 上无法稳定完成 `go test ./...`：缺少前端 embed 产物、流测试并发污染全局 timeout、缓存测试时间键碰撞。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 开发者可重复运行完整测试 (Priority: P1)

开发者按文档准备前端嵌入资源后，可在 Windows 连续运行完整 Go 测试而不因测试互相污染随机崩溃。

**Independent Test**: 在同一干净 worktree 连续三次执行准备流程和 `go test ./... -count=1`，三次均通过。

**Acceptance Scenarios**:

1. **Given** 全局 streaming timeout 尚未初始化或被并发测试暂时恢复为零，**When** 创建流处理 ticker，**Then** 使用安全正默认值而不 panic。
2. **Given** 三个缓存统计测试在 Windows 粗粒度时钟下快速连续执行，**When** 生成测试键，**Then** 每个测试使用确定性唯一键且计数互不污染。
3. **Given** 根包包含前端 embed 指令，**When** 运行全量 Go 测试，**Then** 文档化准备命令先生成所需 dist。

### Edge Cases

- 生产已配置的正 streaming timeout 不得被默认值覆盖。
- 测试修复不得清空或改变生产缓存。
- 前端构建产物保持忽略，不提交生成文件。

## Requirements *(mandatory)*

- **FR-001**: 流处理器 MUST 在非正 timeout 下采用已记录的安全默认值。
- **FR-002**: 正 timeout MUST 保持原值。
- **FR-003**: 缓存测试键 MUST 基于测试身份而非粗粒度当前时间。
- **FR-004**: 全量测试文档 MUST 包含两个前端 dist 的准备步骤。
- **FR-005**: 生成 dist MUST 保持不入库。
- **FR-006**: 修复 MUST 不改变流数据、ping、缓存或生产配置语义。

## Success Criteria *(mandatory)*

- **SC-001**: `go test ./relay/helper ./service -count=1` 连续三轮 100% 通过。
- **SC-002**: 准备 embed 后 `go test ./... -count=1` 连续三轮 100% 通过。
- **SC-003**: 新增回归在 `-race` 可用环境下无全局测试竞态。

## Assumptions

- Bun 可用于生成现有前端 dist；生成物不属于源码提交。
- 本功能不修改支付、计费、缓存生产 key 或前端业务逻辑。
