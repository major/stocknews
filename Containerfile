FROM registry.access.redhat.com/hi/go:1.27-builder@sha256:0682509f458d0ef449f2a3a95e067ecd8fc78bd0d89fc2d0f0cb5d2859908326 AS builder
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.commit=${GIT_SHA} -X main.buildDate=${BUILD_DATE}" -o /tmp/stocknews ./cmd/stocknews

FROM registry.access.redhat.com/hi/static:latest@sha256:20f419d12511f96524d9b9bb092ef5066d6bacee7ed45d7c528bef62f6d48f74
COPY --from=builder /tmp/stocknews /usr/local/bin/stocknews
CMD ["/usr/local/bin/stocknews"]
