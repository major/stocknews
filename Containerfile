FROM registry.access.redhat.com/hi/go:1.26-builder@sha256:f732b1b17f57906dc41e697c7298290c69e472db448d81984195d1841b9db83a AS builder
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.commit=${GIT_SHA} -X main.buildDate=${BUILD_DATE}" -o /tmp/stocknews ./cmd/stocknews

FROM registry.access.redhat.com/hi/static:latest@sha256:f4d5109b57cf7eab0a7adc566f2d78f80fa0c5ec9ccab698c9fb8eb448db6071
COPY --from=builder /tmp/stocknews /usr/local/bin/stocknews
CMD ["/usr/local/bin/stocknews"]
