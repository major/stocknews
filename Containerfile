FROM registry.access.redhat.com/hi/go:1.26-builder@sha256:23a9a544e1c9a8d6b8aed12d671a4620c43536660f9177882cfa08cfdb850fc1 AS builder
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.commit=${GIT_SHA} -X main.buildDate=${BUILD_DATE}" -o /tmp/stocknews ./cmd/stocknews

FROM registry.access.redhat.com/hi/static:latest@sha256:b0771ab538fe1b73abb86aa49e112e7ea966e5893a2b33cf02e9362b865931a8
COPY --from=builder /tmp/stocknews /usr/local/bin/stocknews
CMD ["/usr/local/bin/stocknews"]
