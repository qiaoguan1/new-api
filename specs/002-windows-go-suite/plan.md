# Implementation Plan: Windows 全量 Go 测试稳定性

**Branch**: `codex/144-windows-go-suite` | **Date**: 2026-08-25

## Summary

为未初始化 streaming timeout 添加生产安全默认值；将缓存测试键改为基于 `t.Name()` 的确定性唯一标识；记录前端 dist 准备命令。验证受影响包三轮和准备后的全量三轮。

## Technical Context

**Language**: Go 1.26，兼容 Go 1.22+
**Dependencies**: 标准库 time/testing，现有 Bun 前端构建
**Testing**: Go test、gofmt、go vet、三轮全套
**Constraints**: 不提交 dist，不改变正 timeout，不改生产缓存语义

## Constitution Check

- Production invariants: PASS
- Test-first observable behavior: PASS; reproduced panics/count leakage are RED evidence
- Cross-platform safety: PASS; Windows-specific collision gets deterministic fix
- Reversible delivery: PASS; isolated test-stability PR, no deployment

## Files

```text
relay/helper/stream_scanner.go
relay/helper/stream_scanner_test.go
service/channel_affinity_usage_cache_test.go
docs/testing.md
```

## Complexity Tracking

No violations.
