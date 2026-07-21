FROM golang:1.26.5-alpine AS build

WORKDIR /src
COPY go.mod ./
COPY gateway ./gateway
RUN CGO_ENABLED=0 go test ./gateway/... && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ai-agent-gateway ./gateway/cmd/gateway

FROM scratch

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/ai-agent-gateway /ai-agent-gateway
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/ai-agent-gateway"]
