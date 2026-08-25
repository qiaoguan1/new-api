# Research: Windows 全量 Go 测试稳定性

- **Decision**: Default non-positive streaming timeout to the same 300 seconds used by environment initialization.
  **Rationale**: Tests and defensive startup cannot call `time.NewTicker(0)`; configured positive values remain unchanged.
- **Decision**: Use `t.Name()` in cache keys.
  **Rationale**: It is deterministic and unique within the package; Windows wall-clock resolution is not.
- **Decision**: Build both existing frontends before root Go tests.
  **Rationale**: `go:embed` requires matching files at compile time; placeholder committed files would falsify release behavior.
