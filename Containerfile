FROM registry.access.redhat.com/hi/go:1.26-builder@sha256:c2c7b0e204a331eb392a60cc1fcdeade8cf881f682f8370e5668c5c70d723328 AS builder
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
