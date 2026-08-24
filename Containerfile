FROM registry.access.redhat.com/hi/go:1.26-builder@sha256:70ea7698d8e593b02282cb89c0f5ff63d4fc229385bd600cf84db18ad2cd560a AS builder
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.commit=${GIT_SHA} -X main.buildDate=${BUILD_DATE}" -o /tmp/stocknews ./cmd/stocknews

FROM registry.access.redhat.com/hi/static:latest@sha256:e6e00bcc3803b2faf7de0b08af2e1b21b155da6c891e153caafd99999c083ee1
COPY --from=builder /tmp/stocknews /usr/local/bin/stocknews
CMD ["/usr/local/bin/stocknews"]
