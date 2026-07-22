FROM registry.access.redhat.com/ubi9/go-toolset:1.24 AS builder
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.commit=${GIT_SHA} -X main.buildDate=${BUILD_DATE}" -o /tmp/stocknews ./cmd/stocknews

FROM registry.access.redhat.com/ubi9/ubi-micro
COPY --from=builder /tmp/stocknews /usr/local/bin/stocknews
USER 65532:65532
CMD ["/usr/local/bin/stocknews"]
