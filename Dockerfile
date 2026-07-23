FROM oven/bun:1 AS builder

WORKDIR /build
COPY web/default/package.json .
COPY web/default/bun.lock .
RUN echo '[install]' > bunfig.toml && echo 'registry = "https://registry.npmmirror.com"' >> bunfig.toml && bun install --frozen-lockfile
COPY ./web/default .
COPY ./VERSION .
RUN DISABLE_ESLINT_PLUGIN='true' VITE_REACT_APP_VERSION=$(cat VERSION) bun run build

# Classic 前端用占位文件（服务器内存不足以构建）
FROM alpine:latest AS builder-classic
WORKDIR /build
RUN mkdir -p dist && \
    echo '<!DOCTYPE html><html><head><title>New API</title></head><body><h1>Classic UI</h1><p>Classic UI is not available in this build.</p></body></html>' > dist/index.html && \
    echo 'body{font-family:sans-serif;text-align:center;padding:50px}' > dist/style.css

FROM golang:1.26.1-alpine AS builder2
ENV GO111MODULE=on CGO_ENABLED=0

ARG TARGETOS
ARG TARGETARCH
ENV GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64}
ENV GOEXPERIMENT=greenteagc

WORKDIR /build

ADD go.mod go.sum ./
RUN go mod download

COPY . .
COPY --from=builder /build/dist ./web/default/dist
COPY --from=builder-classic /build/dist ./web/classic/dist
RUN go build -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION)'" -o new-api

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata libasan8 wget \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

COPY --from=builder2 /build/new-api /
EXPOSE 3000
WORKDIR /data
ENTRYPOINT ["/new-api"]
