FROM registry.access.redhat.com/hi/rust:1.96-builder AS builder
WORKDIR /src
COPY . .
RUN cargo build --release --locked

FROM registry.access.redhat.com/hi/rust:1.96
COPY --from=builder /src/target/release/stocknews /usr/local/bin/stocknews
USER 65532:65532
CMD ["/usr/local/bin/stocknews"]
