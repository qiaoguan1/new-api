"""Apply only reviewed Fable changes to the immutable production-source copy."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys


ROOT = Path("/opt/ai-api-stack/releases/issue187-fable")
BASELINE = Path("/opt/ai-api-stack/releases/issue183-runtime/source")


def replace_once(text: str, old: str, new: str) -> str:
    """Reject unexpected source rather than applying a partial broad rewrite."""
    if text.count(old) != 1:
        raise RuntimeError("unexpected production source anchor")
    return text.replace(old, new, 1)


def apply(bundle_path: Path, amend: bool = False) -> None:
    """Keep unrelated production source intact and record every changed file."""
    raw = bundle_path.read_bytes()
    bundle = {name: base64.b64decode(data).decode("utf-8").replace("\r\n", "\n")
              for name, data in json.loads(raw).items()}
    marker = ROOT / "source-amended.json"
    if marker.exists():
        saved = json.loads(marker.read_text())
        for name, digest in saved["changed_sha256"].items():
            if hashlib.sha256((ROOT / "source" / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("previous patch source changed")
        if saved["bundle_sha256"] == hashlib.sha256(raw).hexdigest():
            print("existing exact same Fable source patch verified")
            return
        if not amend:
            raise RuntimeError("source was already patched from a different bundle")
        allowed = {"dto/fable_compat.go", "relay/helper/fable_compat.go", "relay/helper/fable_compat_test.go",
                   "service/fable_cache_billing_test.go", "relay/channel/claude/fable_compat_test.go",
                   "relay/helper/valid_request.go", "relay/channel/claude/relay-claude.go",
                   "service/tiered_settle.go", "service/text_quota.go"}
        allowed |= {"controller/relay.go", "controller/fable_validation_test.go"}
        if not set(saved["changed_sha256"]).issubset(allowed):
            raise RuntimeError("previous patch was not the exact scoped Fable patch")
        archive = ROOT / ("source-revision-" + saved["bundle_sha256"][:12])
        archive.mkdir(mode=0o700)
        for name in sorted(saved["changed_sha256"]):
            current = ROOT / "source" / name
            destination = archive / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, destination)
            if (BASELINE / name).exists():
                shutil.copy2(BASELINE / name, current)
            else:
                # This file was created by this task and its exact hash was
                # checked above. Preserve it in the revision rather than delete.
                current.rename(archive / (name.replace("/", "_") + ".preserved"))
        marker.rename(archive / marker.name)
    new_files = {
        name: bundle[name] for name in ["dto/fable_compat.go", "relay/helper/fable_compat.go",
                                      "relay/helper/fable_compat_test.go", "service/fable_cache_billing_test.go"]
    }
    converter_test = bundle["service/relayconvert/internal/oai_chat/fable_compat_test.go"]
    new_files["relay/channel/claude/fable_compat_test.go"] = converter_test.replace(
        "package oaichat", "package claude", 1).replace("OpenAIChatRequestToClaudeMessages", "RequestOpenAI2ClaudeMessage")
    if "controller/fable_validation_test.go" in bundle:
        new_files["controller/fable_validation_test.go"] = bundle["controller/fable_validation_test.go"]
    patched: dict[str, str] = {}
    source = ROOT / "source"
    validator = (source / "relay/helper/valid_request.go").read_text()
    native_start = validator.index("func GetAndValidateClaudeRequest(")
    text_start = validator.index("func GetAndValidateTextRequest(")
    before, native, text = validator[:native_start], validator[native_start:text_start], validator[text_start:]
    native = replace_once(native, '\tif textRequest.Model == "" {\n\t\treturn nil, errors.New("field model is required")\n\t}',
                          '\tif textRequest.Model == "" {\n\t\treturn nil, errors.New("field model is required")\n\t}\n'
                          '\tif err := validateFable51ProtocolFields(c, textRequest.Model, true); err != nil { return nil, err }\n'
                          '\tif err := dto.NormalizeClaudeFable51Request(textRequest); err != nil { return nil, err }')
    text = replace_once(text, '\tif textRequest.Model == "" {\n\t\treturn nil, errors.New("model is required")\n\t}',
                        '\tif textRequest.Model == "" {\n\t\treturn nil, errors.New("model is required")\n\t}\n'
                        '\tif err := validateFable51ProtocolFields(c, textRequest.Model, false); err != nil { return nil, err }')
    patched["relay/helper/valid_request.go"] = before + native + text
    if 'func validateFable51RelayEndpoint(' in bundle["relay/helper/fable_compat.go"]:
        entry = patched["relay/helper/valid_request.go"]
        start = entry.index('func GetAndValidateRequest(')
        line_end = entry.index('\n', start)
        entry = entry[:line_end + 1] + '\tif err := validateFable51RelayEndpoint(c, format); err != nil { return nil, err }\n' + entry[line_end + 1:]
        patched["relay/helper/valid_request.go"] = entry
    modern = bundle["service/relayconvert/internal/oai_chat/to_claude_messages_req.go"]
    validation_start = modern.index('\tif textRequest.Model == dto.ClaudeFable51Model {', modern.index('func OpenAIChatRequestToClaudeMessages'))
    validation_end = modern.index('\tclaudeTools :=', validation_start)
    validation = modern[validation_start:validation_end]
    adaptive_start = modern.index('\tif textRequest.Model == dto.ClaudeFable51Model {', validation_end)
    adaptive_end = modern.index('\t} else if textRequest.ReasoningEffort != "" {', adaptive_start) + len('\t} else if textRequest.ReasoningEffort != "" {')
    adaptive = modern[adaptive_start:adaptive_end]
    old_converter = (source / "relay/channel/claude/relay-claude.go").read_text()
    old_converter = replace_once(old_converter,
                                'func RequestOpenAI2ClaudeMessage(c *gin.Context, textRequest dto.GeneralOpenAIRequest) (*dto.ClaudeRequest, error) {\n',
                                'func RequestOpenAI2ClaudeMessage(c *gin.Context, textRequest dto.GeneralOpenAIRequest) (*dto.ClaudeRequest, error) {\n' + validation)
    patched["relay/channel/claude/relay-claude.go"] = replace_once(old_converter, '\n\tif textRequest.ReasoningEffort != "" {', '\n' + adaptive)
    modern_tier = bundle["service/tiered_settle.go"]
    wrapper = modern_tier[modern_tier.index('// BuildModelTieredTokenParams'):modern_tier.index('// BuildTieredTokenParams constructs')]
    patched["service/tiered_settle.go"] = replace_once((source / "service/tiered_settle.go").read_text(),
                                                      '// BuildTieredTokenParams constructs', wrapper + '// BuildTieredTokenParams constructs')
    patched["service/text_quota.go"] = replace_once((source / "service/text_quota.go").read_text(),
                                                    'BuildTieredTokenParams(usage, summary.IsClaudeUsageSemantic, tieredUsedVars)',
                                                    'BuildModelTieredTokenParams(relayInfo.OriginModelName, usage, summary.IsClaudeUsageSemantic, tieredUsedVars)')
    patched["controller/relay.go"] = replace_once((source / "controller/relay.go").read_text(),
        '\t\t} else {\n\t\t\tnewAPIError = types.NewError(err, types.ErrorCodeInvalidRequest)\n\t\t}',
        '\t\t} else if c.GetBool(helper.Fable51RequestContextKey) {\n'
        '\t\t\tnewAPIError = types.NewError(err, types.ErrorCodeInvalidRequest, types.ErrOptionWithStatusCode(http.StatusBadRequest))\n'
        '\t\t} else {\n\t\t\tnewAPIError = types.NewError(err, types.ErrorCodeInvalidRequest)\n\t\t}')
    changed = {**patched, **new_files}
    for name in changed:
        current = source / name
        baseline = BASELINE / name
        if name in new_files:
            if current.exists() or baseline.exists():
                raise RuntimeError("new Fable file collides with baseline")
        elif current.read_bytes() != baseline.read_bytes():
            raise RuntimeError("candidate baseline changed before patch")
    for name, content in changed.items():
        (source / name).write_text(content, encoding="utf-8")
    subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", str(source) + ":/build",
                    "golang:1.26.1-alpine", "gofmt", "-w", *["/build/" + name for name in changed]], check=True)
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in changed}
    marker.write_text(json.dumps({"bundle_sha256": hashlib.sha256(raw).hexdigest(), "changed_sha256": hashes,
                                  "new_files": sorted(new_files)}, indent=2), encoding="utf-8")
    print(json.dumps({"fable_only_source_files": sorted(changed)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    apply(Path(sys.argv[1]), amend="--amend" in sys.argv[2:])
