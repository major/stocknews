FROM docker.io/library/rust:1.96 AS builder
WORKDIR /src
COPY . .
RUN cargo build --release --locked

FROM docker.io/library/debian:stable-slim
COPY --from=builder /src/target/release/stocknews /usr/local/bin/stocknews
USER 65532:65532
CMD ["/usr/local/bin/stocknews"]
